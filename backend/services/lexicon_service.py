"""
词库业务逻辑 — 词条 CRUD、领域映射（词条×领域模板→实体 code）、缓存（查询扩展 + 图谱词条）、CSV/IK 导出。

职责边界（v2 docs/10 §7 / docs/plan/48）：
- 词条（term+aliases）为全局词典：服务 ES 分词（IK 词典导出）与查询同义词扩展（expand_query）；
- 词条与图谱的关系由“领域映射”表达：词条仅在绑定 KB 模板下存在映射时才会进图（建实体/归并/检索候选）；
- 无 category（全局固定分类已废弃）。

缓存约定：
- `_cache`：启用词条 标准词 → {"aliases": [...]}（分词/同义词/图谱别名匹配用）；
- 词条/映射任何增删改/导入后：refresh_cache() + 图谱构建状态全量失效（保守，词库影响图谱内容）；
- 领域映射按需读库（lexicon_domain_mappings 表小），不做进程缓存。
"""
import csv
import io
from typing import Any

from core.database import async_session
from repositories.lexicon_mapping_repository import LexiconMappingRepository
from repositories.lexicon_repository import LexiconRepository
from services import domain_service
from utils.logging import get_logger

logger = get_logger(__name__)

# 标准词 → {"aliases": [...]}（仅启用词条）
_cache: dict[str, dict] | None = None


# ============================================================
# 缓存
# ============================================================

async def _load_cache() -> dict[str, dict]:
    async with async_session() as session:
        repo = LexiconRepository(session)
        terms = await repo.list_enabled()
    return {
        t.term: {"aliases": list(t.aliases or [])}
        for t in terms
        if t.term
    }


async def refresh_cache() -> dict[str, dict]:
    """重新加载词库缓存（词条变更后调用）。"""
    global _cache
    _cache = await _load_cache()
    logger.info("词库缓存已刷新: %d 词条", len(_cache))
    return _cache


async def ensure_cache() -> dict[str, dict]:
    """首次使用前加载缓存。"""
    global _cache
    if _cache is None:
        await refresh_cache()
    return _cache or {}


# ============================================================
# 查询扩展（分词/同义词，不依赖领域映射）
# ============================================================

def expand_query(query: str) -> list[str]:
    """查询扩展：把命中的别名/标准词并入查询词列表（OR 语义）。

    规则：
    - query 含某词条的别名 → 加入该标准词；
    - query 含某词条的标准词 → 加入其全部别名。
    返回 [原 query, *扩展词]；无命中时仅返回原 query。
    """
    cache = _cache or {}
    add_terms: set[str] = set()
    for term, meta in cache.items():
        aliases = meta["aliases"]
        if term in query:
            add_terms.update(a for a in aliases if a)
        else:
            for alias in aliases:
                if alias and alias in query:
                    add_terms.add(term)
                    break
    if not add_terms:
        return [query]
    return [query, *sorted(add_terms)]


# ============================================================
# 领域映射（图谱使用：词条在 KB 绑定模板下的实体归属）
# ============================================================

async def profile_terms(profile_id: str | None) -> list[dict]:
    """该模板下启用词条的映射：[{term, aliases, code}]（图谱抽取/检索候选；空映射返回 []）。"""
    if not profile_id:
        return []
    await ensure_cache()
    async with async_session() as session:
        pairs = await LexiconMappingRepository(session).list_by_profile(profile_id)
    cache = _cache or {}
    items: list[dict] = []
    for term, code in pairs:
        meta = cache.get(term) or {}
        if code:
            items.append({"term": term, "aliases": list(meta.get("aliases") or []), "code": code})
    return items


def match_terms(text: str, terms: list[dict]) -> list[tuple[str, str]]:
    """图谱候选：文本命中词条（标准词/别名，长词优先）→ [(标准词, code)]，去重。"""
    hits: list[tuple[int, str, str]] = []
    for t in terms:
        word = t["term"]
        if not word or not t.get("code"):
            continue
        if word in text:
            hits.append((len(word), word, t["code"]))
            continue
        for alias in t.get("aliases") or []:
            if alias and alias in text:
                hits.append((len(word), word, t["code"]))
                break
    hits.sort(key=lambda x: x[0], reverse=True)
    seen: dict[str, str] = {}
    for _, word, code in hits:
        seen.setdefault(word, code)
    return [(w, c) for w, c in seen.items()]


async def _profiles_by_id() -> dict[str, dict]:
    profiles = await domain_service.list_profiles()
    return {p["id"]: p for p in profiles}


async def list_term_mappings(term_id: str) -> list[dict]:
    """词条领域映射视图：[{domain_profile_id, domain_name, entity_code, entity_name}]。"""
    async with async_session() as session:
        rows = await LexiconMappingRepository(session).list_by_term(term_id)
    profiles = await _profiles_by_id()
    items: list[dict] = []
    for m in rows:
        p = profiles.get(m.domain_profile_id)
        if p is None:
            continue  # 模板已删（防御）
        meta = p["entity_meta"].get(m.entity_code, {})
        items.append({
            "domain_profile_id": m.domain_profile_id,
            "domain_name": p["name"],
            "entity_code": m.entity_code,
            "entity_name": meta.get("name", m.entity_code),
        })
    return items


async def replace_term_mappings(term_id: str, items: list[dict[str, str]]) -> list[dict]:
    """整包替换词条领域映射（items: [{domain_profile_id, entity_code}]）。

    校验：词条存在；领域存在且不重复；entity_code 必须是该模板实体 code。
    """
    async with async_session() as session:
        if await LexiconRepository(session).get_by_id(term_id) is None:
            raise ValueError("词条不存在")
    profiles = await _profiles_by_id()
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for item in items:
        pid = (item.get("domain_profile_id") or "").strip()
        code = (item.get("entity_code") or "").strip()
        if not pid or not code:
            raise ValueError("领域与实体类型不能为空")
        p = profiles.get(pid)
        if p is None:
            raise ValueError(f"领域模板不存在: {pid}")
        if pid in seen:
            raise ValueError(f"同一领域只允许一个实体类型（重复: {p['name']}）")
        if code not in p["entity_meta"]:
            raise ValueError(f"实体类型 {code} 不在领域「{p['name']}」中")
        seen.add(pid)
        pairs.append((pid, code))
    async with async_session() as session:
        await LexiconMappingRepository(session).replace_for_term(term_id, pairs)
    await _invalidate_graphs_after_lexicon_change()
    return await list_term_mappings(term_id)


# ============================================================
# 词条 CRUD（分词/同义词全局词典）
# ============================================================

async def _invalidate_graphs_after_lexicon_change() -> None:
    """词库变更联动：词条/领域映射变化会影响已建图谱 → 文档图谱状态置 none（提示重建）。

    best-effort：失败仅记日志，不阻断词库操作；重建走"清旧重建"幂等兜底。
    """
    try:
        from repositories.document_repository import DocumentRepository

        async with async_session() as session:
            await DocumentRepository(session).invalidate_all_graphs()
    except Exception as e:
        logger.error("词库变更联动重置图谱状态失败（忽略）: %s", e)


async def create_term(
    term: str,
    aliases: list[str] | None = None,
    enabled: bool = True,
) -> dict:
    async with async_session() as session:
        repo = LexiconRepository(session)
        if await repo.get_by_term(term) is not None:
            raise ValueError(f"词条已存在: {term}")
        entry = await repo.create(term, aliases, enabled)
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()
    return _to_dict(entry)


async def list_terms(
    page: int = 1, page_size: int = 20, search: str = ""
) -> dict:
    offset = (page - 1) * page_size
    async with async_session() as session:
        repo = LexiconRepository(session)
        rows, total = await repo.list_page(offset, page_size, search)
        term_ids = [str(t.id) for t in rows]
        counts = await LexiconMappingRepository(session).count_by_terms(term_ids)
    return {
        "items": [
            {**_to_dict(t), "mappings_count": counts.get(str(t.id), 0)}
            for t in rows
        ],
        "total": total,
    }


async def update_term(term_id: str, data: dict[str, Any]) -> dict:
    async with async_session() as session:
        repo = LexiconRepository(session)
        entry = await repo.get_by_id(term_id)
        if entry is None:
            raise ValueError("词条不存在")
        new_term = data.get("term")
        if new_term is not None and new_term != entry.term:
            if await repo.get_by_term(new_term) is not None:
                raise ValueError(f"词条已存在: {new_term}")
        updated = await repo.update(term_id, **data)
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()
    return _to_dict(updated)


async def delete_term(term_id: str) -> None:
    async with async_session() as session:
        repo = LexiconRepository(session)
        if await repo.get_by_id(term_id) is None:
            raise ValueError("词条不存在")
        await LexiconMappingRepository(session).delete_by_term(term_id)
        await repo.delete(term_id)
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()


async def get_stats() -> dict:
    async with async_session() as session:
        repo = LexiconRepository(session)
        return await repo.stats()


# ============================================================
# 导出 / 导入
# ============================================================

async def export_ik_dict() -> str:
    """IK 远程词典：一行一个启用词条（ES 分词用）。"""
    async with async_session() as session:
        repo = LexiconRepository(session)
        terms = await repo.list_enabled()
    return "\n".join(t.term for t in terms)


async def export_csv() -> str:
    """导出全部词条 CSV（词条,别名(;分隔)）。"""
    async with async_session() as session:
        repo = LexiconRepository(session)
        rows = list((await repo.list_page(0, 100000))[0])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["词条", "别名"])
    for t in rows:
        writer.writerow([t.term, ";".join(t.aliases or [])])
    return buf.getvalue()


async def import_csv(content: str) -> dict:
    """批量导入 CSV（每行：词条,别名(;分隔)；旧文件第 3 列分类自动忽略；首行表头自动跳过）。"""
    imported = 0
    skipped = 0
    errors: list[str] = []
    reader = csv.reader(io.StringIO(content))
    async with async_session() as session:
        repo = LexiconRepository(session)
        for lineno, row in enumerate(reader, start=1):
            if not row or not row[0].strip():
                continue
            term = row[0].strip()
            if term in ("词条", "term"):
                continue  # 表头
            aliases = [a.strip() for a in row[1].split(";")] if len(row) > 1 and row[1] else []
            if await repo.get_by_term(term) is not None:
                skipped += 1
                continue
            try:
                await repo.create(term, aliases)
                imported += 1
            except Exception as e:
                errors.append(f"第 {lineno} 行: {e}")
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()
    return {"imported": imported, "skipped": skipped, "errors": errors}


def _to_dict(entry) -> dict:
    return {
        "id": str(entry.id),
        "term": entry.term,
        "aliases": list(entry.aliases or []),
        "enabled": entry.enabled,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }
