"""
词库业务逻辑 — 词条 CRUD、内存缓存（查询扩展 + 图谱实体匹配）、IK 词典/CSV 导出。

缓存约定：
- 内存缓存仅含启用词条：标准词 → {"aliases": [...], "category": "..."}；
- 任何增删改/导入后调用 refresh_cache()，查询扩展/图谱匹配即时生效；
- IK 远程词典热更新受 IK Monitor 轮询间隔限制（约 1 小时），由导出端点兜底。

词库分类（category）为固定枚举（图谱实体类型映射依据，§09 设计 5.2.1）：
organization / project / person / location / qualification / other
"""
import csv
import io
from typing import Any

from core.database import async_session
from repositories.lexicon_repository import LexiconRepository
from utils.logging import get_logger

logger = get_logger(__name__)

# 标准词 → {"aliases": [...], "category": "..."}（仅启用词条）
_cache: dict[str, dict] | None = None

# 词库分类 → 图谱实体类型标签（通用本体，other 不建图谱节点）
LEXICON_CATEGORY_ENTITY_TYPES = {
    "organization": "Organization",
    "person": "Person",
    "place": "Place",
    "project": "Project",
    "product": "Product",
    "concept": "Concept",
}

# 旧自由文本分类 → 枚举（历史数据兼容；location→place、qualification→concept 为通用化映射）
_CATEGORY_LEGACY_MAP = {
    "公司": "organization", "企业": "organization", "单位": "organization", "机构": "organization",
    "项目": "project", "工程": "project",
    "人员": "person", "人名": "person",
    "地点": "place", "位置": "place", "location": "place",
    "资质": "concept", "证书": "concept", "标准": "concept", "技能": "concept",
    "概念": "concept", "qualification": "concept",
}


def normalize_category(category: str) -> str:
    """自由文本/枚举 → 枚举值；无法识别归 other。"""
    cat = (category or "").strip()
    if cat in LEXICON_CATEGORY_ENTITY_TYPES:
        return cat
    return _CATEGORY_LEGACY_MAP.get(cat, "other")


async def _load_cache() -> dict[str, dict]:
    async with async_session() as session:
        repo = LexiconRepository(session)
        terms = await repo.list_enabled()
    return {
        t.term: {"aliases": list(t.aliases or []), "category": t.category or ""}
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


def match_entities(text: str) -> list[dict]:
    """扫描文本，最长匹配启用词条（标准词/别名），返回图谱实体候选。

    规则：
    - 词条长度降序匹配（长词优先，降低子串误命中）；
    - 命中别名 → 归一到标准词作为实体名（天然去重）；
    - category 映射到图谱实体类型；other/未知分类跳过（通用术语不进图）。

    Returns:
        [{name: 标准词, type: 实体类型}]（按词条长度降序）
    """
    cache = _cache or {}
    candidates: list[tuple[int, str, str]] = []  # (词长, name, type)
    for term, meta in cache.items():
        etype = LEXICON_CATEGORY_ENTITY_TYPES.get(normalize_category(meta["category"]))
        if etype is None:
            continue
        if term in text:
            candidates.append((len(term), term, etype))
            continue
        for alias in meta["aliases"]:
            if alias and alias in text:
                candidates.append((len(term), term, etype))
                break
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [{"name": name, "type": etype} for _, name, etype in candidates]


async def _invalidate_graphs_after_lexicon_change() -> None:
    """词库变更联动（风险 10）：已构建文档图谱状态置 none（stale 标记，前端提示重建）。

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
    category: str = "",
    enabled: bool = True,
) -> dict:
    async with async_session() as session:
        repo = LexiconRepository(session)
        if await repo.get_by_term(term) is not None:
            raise ValueError(f"词条已存在: {term}")
        entry = await repo.create(term, aliases, category, enabled)
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()
    return _to_dict(entry)


async def list_terms(
    page: int = 1, page_size: int = 20, search: str = "", category: str = ""
) -> dict:
    offset = (page - 1) * page_size
    async with async_session() as session:
        repo = LexiconRepository(session)
        rows, total = await repo.list_page(offset, page_size, search, category)
    return {"items": [_to_dict(t) for t in rows], "total": total}


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
        if await repo.delete(term_id) is None:
            raise ValueError("词条不存在")
    await refresh_cache()
    await _invalidate_graphs_after_lexicon_change()


async def get_stats() -> dict:
    async with async_session() as session:
        repo = LexiconRepository(session)
        return await repo.stats()


async def export_ik_dict() -> str:
    """IK 远程词典：一行一个启用词条。"""
    async with async_session() as session:
        repo = LexiconRepository(session)
        terms = await repo.list_enabled()
    return "\n".join(t.term for t in terms)


async def export_csv() -> str:
    """导出全部词条 CSV（词条,别名(;分隔),分类）。"""
    async with async_session() as session:
        repo = LexiconRepository(session)
        rows = list((await repo.list_page(0, 100000))[0])
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["词条", "别名", "分类"])
    for t in rows:
        writer.writerow([t.term, ";".join(t.aliases or []), t.category])
    return buf.getvalue()


async def import_csv(content: str) -> dict:
    """批量导入 CSV（每行：词条,别名(;分隔),分类；首行表头自动跳过）。

    分类自动规范化：中文常见值（公司/项目/人员/地点/资质）映射为枚举，
    未知值归 other（见 normalize_category）。
    """
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
            category = normalize_category(row[2].strip()) if len(row) > 2 else ""
            if await repo.get_by_term(term) is not None:
                skipped += 1
                continue
            try:
                await repo.create(term, aliases, category)
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
        "category": entry.category,
        "enabled": entry.enabled,
        "created_at": entry.created_at.isoformat() if entry.created_at else "",
        "updated_at": entry.updated_at.isoformat() if entry.updated_at else "",
    }
