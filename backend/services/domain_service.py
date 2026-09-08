"""
领域模板服务 — 缓存/解析 + 类型校验 + 抽取提示词生成（v2 图谱本体单一事实源）。

数据流（docs/10-generic-kb-v2-design.md §3.1）：
- KB 绑定 domain_profile_id → 取模板；未绑定/模板缺失 → 回退内置"通用"预设；
- 模板解析为紧凑结构（entity_codes/entity_meta/relation_names/relation_meta）供抽取过滤与前端 meta；
- 抽取提示词由模板生成（build_extraction_prompt），不再读 DB entity_extraction 模板。

缓存约定（沿用 lexicon_service 风格）：模块级 _cache；模板 CRUD 后调用 invalidate()。
"""
from typing import Any

from sqlalchemy.exc import IntegrityError

from core.database import async_session
from core.domain_presets import (
    BID_PRESET_ID,
    ENTITY_CODE_RE,
    ENTITY_COUNT_MAX,
    ENTITY_COUNT_MIN,
    GENERIC_PRESET,
    GENERIC_PRESET_ID,
    RELATION_COUNT_MAX,
    RESERVED_LABELS,
)
from models.domain import DomainProfile
from repositories.document_repository import DocumentRepository
from repositories.domain_repository import DomainRepository
from utils.logging import get_logger

logger = get_logger(__name__)

# 缓存: {profile_id: ProfileData}；None = 未加载
_cache: dict[str, dict] | None = None


class DomainValidationError(ValueError):
    """模板业务规则冲突（400：名称重复/内置预设受保护等）。"""


class DomainInUseError(ValueError):
    """模板仍被知识库引用等使用中冲突（409）。"""


# ============================================================
# 纯函数：数据结构化 / 校验 / 提示词生成（可单测，不触库）
# ============================================================

def _to_profile_data(profile: DomainProfile) -> dict:
    """ORM 行 → 紧凑结构。"""
    entity_meta: dict[str, dict] = {}
    for e in profile.entity_types or []:
        code = e.get("code", "")
        entity_meta[code] = {
            "name": e.get("name", code),
            "color": e.get("color", "#909399"),
            "description": e.get("description", ""),
            "examples": e.get("examples", []),
        }
    relation_meta: dict[str, dict] = {}
    for r in profile.relation_types or []:
        rname = r.get("name", "")
        relation_meta[rname] = {
            "description": r.get("description", ""),
            "direction_hint": r.get("direction_hint", ""),
        }
    return {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description or "",
        "is_preset": profile.is_preset,
        "entity_types": profile.entity_types or [],        # 原始有序清单（提示词生成用）
        "relation_types": profile.relation_types or [],    # 原始有序清单（提示词生成用）
        "entity_codes": set(entity_meta.keys()),
        "entity_meta": entity_meta,
        "relation_names": set(relation_meta.keys()),
        "relation_meta": relation_meta,
    }


def _preset_data(preset: dict, preset_id: str) -> dict:
    """内置预设常量 → 与 _to_profile_data 相同结构（缺省回退用）。"""
    entity_meta: dict[str, dict] = {}
    for e in preset["entity_types"]:
        entity_meta[e["code"]] = {
            "name": e["name"], "color": e["color"],
            "description": e["description"], "examples": e.get("examples", []),
        }
    relation_meta: dict[str, dict] = {}
    for r in preset["relation_types"]:
        relation_meta[r["name"]] = {
            "description": r["description"], "direction_hint": r.get("direction_hint", ""),
        }
    return {
        "id": preset_id,
        "name": preset["name"],
        "description": preset.get("description", ""),
        "is_preset": True,
        "entity_types": preset["entity_types"],            # 原始有序清单（提示词生成用）
        "relation_types": preset["relation_types"],        # 原始有序清单（提示词生成用）
        "entity_codes": set(entity_meta.keys()),
        "entity_meta": entity_meta,
        "relation_names": set(relation_meta.keys()),
        "relation_meta": relation_meta,
    }


def default_profile() -> dict:
    """内置通用预设兜底数据（图谱链路缺省/异常时使用）。"""
    return _generic_fallback()


def validate_profile(name: str, entity_types: list[dict], relation_types: list[dict]) -> list[str]:
    """模板输入校验，返回错误列表（空 = 通过）。约束见 core/domain_presets 常量。"""
    errors: list[str] = []
    if not name or not name.strip():
        errors.append("模板名称不能为空")
    elif len(name.strip()) > 100:
        errors.append("模板名称不能超过 100 字符")

    if not (ENTITY_COUNT_MIN <= len(entity_types) <= ENTITY_COUNT_MAX):
        errors.append(f"实体类型数量须在 {ENTITY_COUNT_MIN}..{ENTITY_COUNT_MAX} 之间（当前 {len(entity_types)}）")
    seen_codes: set[str] = set()
    for i, e in enumerate(entity_types or []):
        code = (e.get("code") or "").strip()
        if not code:
            errors.append(f"第 {i + 1} 个实体类型缺少 code")
            continue
        if not ENTITY_CODE_RE.match(code):
            errors.append(f"实体 code「{code}」非法：仅允许字母开头的大小写字母/数字/下划线")
        if code in RESERVED_LABELS:
            errors.append(f"实体 code「{code}」为系统保留标签，不可使用")
        if code in seen_codes:
            errors.append(f"实体 code「{code}」重复")
        seen_codes.add(code)
        if not (e.get("name") or "").strip():
            errors.append(f"实体 code「{code}」缺少中文名")
        color = (e.get("color") or "").strip()
        if color and not (color.startswith("#") and len(color) == 7):
            errors.append(f"实体 code「{code}」颜色须为 #RRGGBB 格式")

    if len(relation_types or []) > RELATION_COUNT_MAX:
        errors.append(f"关系类型数量不能超过 {RELATION_COUNT_MAX}")
    seen_relations: set[str] = set()
    for i, r in enumerate(relation_types or []):
        rname = (r.get("name") or "").strip()
        if not rname:
            errors.append(f"第 {i + 1} 个关系类型缺少名称")
            continue
        if rname in seen_relations:
            errors.append(f"关系名称「{rname}」重复")
        seen_relations.add(rname)
    return errors


def build_extraction_prompt(entity_types: list[dict], relation_types: list[dict]) -> str:
    """由模板生成 schema 约束抽取提示词（设计 §3.2 / 方案 47 伪代码）。"""
    lines = [
        "你是信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。",
        "实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体重复出现只需输出一次。",
        "",
        "实体类型（仅限以下）:",
    ]
    for e in entity_types:
        code = e.get("code", "")
        name = e.get("name", code)
        desc = e.get("description", "")
        examples = e.get("examples") or []
        suffix = f" 示例：{'、'.join(examples)}" if examples else ""
        lines.append(f"- {code}({name})：{desc}{suffix}")
    lines += ["", "关系类型（仅限以下，方向见括号）:"]
    for r in relation_types:
        rname = r.get("name", "")
        hint = f"（{r['direction_hint']}）" if r.get("direction_hint") else ""
        desc = r.get("description", "")
        lines.append(f"- {rname}{hint}：{desc}")
    lines += [
        "",
        "输出严格 JSON（不要其他文字，不要 markdown 代码块）:",
        '{"entities":[{"name":"...","type":"ENTITY_TYPE","description":"..."}],',
        ' "relationships":[{"source":"...","target":"...","type":"RELATION_TYPE","description":"..."}]}',
    ]
    return "\n".join(lines)


def profile_meta(profile: dict) -> dict:
    """紧凑结构 → API meta 视图（前端渲染单一来源）。"""
    return {
        "id": profile["id"],
        "name": profile["name"],
        "description": profile.get("description", ""),
        "is_preset": profile["is_preset"],
        "entity_types": [
            {"code": code, "name": meta["name"], "color": meta["color"],
             "description": meta["description"], "examples": meta.get("examples", [])}
            for code, meta in profile["entity_meta"].items()
        ],
        "relation_types": [
            {"name": rname, "description": meta["description"],
             "direction_hint": meta["direction_hint"]}
            for rname, meta in profile["relation_meta"].items()
        ],
    }


# ============================================================
# 缓存与解析（触库）
# ============================================================

async def _load_cache() -> dict[str, dict]:
    async with async_session() as session:
        rows = await DomainRepository(session).list_all()
    return {p.id: _to_profile_data(p) for p in rows}


def _cache_snapshot() -> dict[str, dict]:
    """返回当前缓存；未加载返回空（触发方自行 ensure_cache）。"""
    return _cache or {}


async def ensure_cache() -> None:
    """惰性加载缓存（首次调用/失效后重建）。"""
    global _cache
    if _cache is None:
        try:
            _cache = await _load_cache()
        except Exception as e:
            logger.error("领域模板缓存加载失败（使用内置通用预设兜底）: %s", e)
            _cache = {}


async def refresh_cache() -> None:
    global _cache
    _cache = await _load_cache()


def invalidate_cache() -> None:
    """模板 CRUD 后调用，置 None 触发下次懒加载。"""
    global _cache
    _cache = None


def _generic_fallback() -> dict:
    """内置通用预设兜底（缓存空/DB 异常时保证图谱链路可用）。"""
    return _preset_data(GENERIC_PRESET, GENERIC_PRESET_ID)


async def get_profile(profile_id: str) -> dict | None:
    """按 id 取模板紧凑结构（缓存优先；未命中回源单查）。"""
    await ensure_cache()
    cached = _cache_snapshot().get(profile_id)
    if cached is not None:
        return cached
    async with async_session() as session:
        row = await DomainRepository(session).get_by_id(profile_id)
    return _to_profile_data(row) if row else None


async def get_profile_for_kb(kb_id: str) -> dict:
    """KB → 模板；未绑定/绑定失效 → 内置通用预设（图谱链路永不空）。"""
    await ensure_cache()
    try:
        async with async_session() as session:
            bound = await DomainRepository(session).get_kb_domain_profile_id(kb_id)
    except Exception as e:
        logger.warning("查询 KB 领域模板失败，回退通用预设: kb=%s err=%s", kb_id, e)
        return _generic_fallback()
    if bound:
        profile = _cache_snapshot().get(bound) if _cache else None
        if profile is None:
            profile = await get_profile(bound)
        if profile is not None:
            return profile
        logger.warning("KB 绑定模板不存在，回退通用预设: kb=%s profile=%s", kb_id, bound)
    # 未绑定 → 通用预设行（按固定种子 id 取；缓存缺失时用常量兜底）
    if _cache and GENERIC_PRESET_ID in _cache:
        return _cache[GENERIC_PRESET_ID]
    return _generic_fallback()


# ============================================================
# 模板 CRUD / 复制 / KB 绑定（路由层调用，内部维护缓存与联动）
# ============================================================

async def list_profiles() -> list[dict]:
    """全部模板紧凑结构（内置预设在前，其后按名称）。"""
    await ensure_cache()
    profiles = list(_cache_snapshot().values())
    profiles.sort(key=lambda p: (not p["is_preset"], p["name"]))
    return profiles


async def create_profile(
    name: str,
    entity_types: list[dict],
    relation_types: list[dict],
    description: str = "",
) -> dict:
    """新建自定义模板；名称重复 → DomainValidationError。"""
    name = (name or "").strip()
    errors = validate_profile(name, entity_types, relation_types)
    if errors:
        raise DomainValidationError("；".join(errors))
    try:
        async with async_session() as session:
            row = await DomainRepository(session).create(
                name=name, entity_types=entity_types,
                relation_types=relation_types, description=description,
            )
    except IntegrityError as e:
        raise DomainValidationError(f"模板名称已存在: {name}") from e
    invalidate_cache()
    return _to_profile_data(row)


async def update_profile(
    profile_id: str,
    name: str,
    description: str,
    entity_types: list[dict],
    relation_types: list[dict],
) -> dict | None:
    """整包更新自定义模板（内置预设不可编辑），并联动失效引用 KB 图谱状态。"""
    async with async_session() as session:
        repo = DomainRepository(session)
        row = await repo.get_by_id(profile_id)
        if row is None:
            return None
        if row.is_preset:
            raise DomainValidationError("内置预设不可编辑，可复制后修改")
    name = (name or "").strip()
    errors = validate_profile(name, entity_types, relation_types)
    if errors:
        raise DomainValidationError("；".join(errors))
    try:
        async with async_session() as session:
            updated = await DomainRepository(session).update(
                profile_id, name=name, description=description,
                entity_types=entity_types, relation_types=relation_types,
            )
    except IntegrityError as e:
        raise DomainValidationError(f"模板名称已存在: {name}") from e
    invalidate_cache()
    await reset_kb_graphs_of_profile(profile_id)
    return _to_profile_data(updated)


async def delete_profile(profile_id: str) -> bool:
    """删除自定义模板；内置预设受保护；仍有 KB 引用 → DomainInUseError。"""
    async with async_session() as session:
        repo = DomainRepository(session)
        row = await repo.get_by_id(profile_id)
        if row is None:
            return False
        if row.is_preset:
            raise DomainValidationError("内置预设不可删除")
        refs = await repo.count_kb_refs(profile_id)
        if refs > 0:
            raise DomainInUseError(f"仍有 {refs} 个知识库使用该模板，请先为这些知识库更换模板")
        await repo.delete(profile_id)
    invalidate_cache()
    return True


async def count_profile_refs(profile_id: str) -> int:
    """引用该模板的知识库数量（模板详情展示/删除校验用）。"""
    async with async_session() as session:
        return await DomainRepository(session).count_kb_refs(profile_id)


async def copy_profile(profile_id: str) -> dict | None:
    """复制模板为可编辑副本（名称『原（副本）』，冲突自动加序号）。"""
    source = await get_profile(profile_id)
    if source is None:
        return None
    await ensure_cache()
    existing_names = {p["name"] for p in _cache_snapshot().values()}
    base = f"{source['name']}（副本）"
    name = base
    i = 2
    while name in existing_names:
        name = f"{base} {i}"
        i += 1
        if i > 50:
            raise DomainValidationError("副本命名冲突过多，请先清理同名模板")
    return await create_profile(
        name=name,
        entity_types=source["entity_types"],
        relation_types=source["relation_types"],
        description=f"{source.get('description', '')}（副本）",
    )


async def reset_kb_graphs_of_profile(profile_id: str) -> int:
    """模板变更联动：引用该模板的全部 KB 图谱状态 → none（等待重建）。"""
    async with async_session() as session:
        kb_ids = await DomainRepository(session).list_kb_ids_by_profile(profile_id)
    if not kb_ids:
        return 0
    async with async_session() as session:
        doc_repo = DocumentRepository(session)
        for kb_id in kb_ids:
            try:
                await doc_repo.reset_graph_status_by_kb(kb_id)
            except Exception as e:
                logger.warning("模板变更失效 KB 图谱状态失败: kb=%s err=%s", kb_id, e)
    logger.info("模板变更联动失效图谱: profile=%s kbs=%d", profile_id, len(kb_ids))
    return len(kb_ids)


async def bind_kb_profile(kb_id: str, profile_id: str) -> dict | None:
    """KB 换绑领域模板（图谱状态联动失效）；KB 不存在返回 None。"""
    async with async_session() as session:
        kb = await DomainRepository(session).bind_kb(kb_id, profile_id)
        if kb is None:
            return None
    # 换绑 = 本体变化：失效图谱构建状态，等待重建（方案 47 Step 4）
    async with async_session() as session:
        await DocumentRepository(session).reset_graph_status_by_kb(kb_id)
    return await get_profile(profile_id)
