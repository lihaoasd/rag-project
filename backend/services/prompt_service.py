"""
提示词模板业务逻辑 — CRUD + 激活管理 + 预览渲染。
"""
import uuid

from sqlalchemy import select, update

from core.database import async_session
from core.prompt_renderer import render_prompt, extract_variables
from models.prompt_template import PromptTemplate
from utils.logging import get_logger

logger = get_logger(__name__)

SCENARIOS = ["rag_qa", "entity_extraction", "document_summary", "custom", "image_recognition"]


def _to_dict(pt: PromptTemplate) -> dict:
    """ORM 对象 → 响应字典。"""
    return {
        "id": str(pt.id),
        "name": pt.name,
        "description": pt.description,
        "scenario": pt.scenario,
        "system_prompt": pt.system_prompt,
        "user_prompt": pt.user_prompt,
        "variables": pt.variables,
        "is_active": pt.is_active,
        "is_default": pt.is_default,
        "sort_order": pt.sort_order,
        "created_at": pt.created_at.isoformat() if pt.created_at else "",
        "updated_at": pt.updated_at.isoformat() if pt.updated_at else "",
    }


async def list_prompts(scenario: str | None = None) -> list[dict]:
    """获取模板列表，支持按场景筛选。"""
    async with async_session() as session:
        stmt = select(PromptTemplate).order_by(PromptTemplate.sort_order, PromptTemplate.created_at)
        if scenario:
            stmt = stmt.where(PromptTemplate.scenario == scenario)
        result = await session.execute(stmt)
        templates = result.scalars().all()
        return [_to_dict(t) for t in templates]


async def get_prompt(prompt_id: str) -> dict | None:
    """获取单个模板详情。"""
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        )
        pt = result.scalar_one_or_none()
        return _to_dict(pt) if pt else None


async def create_prompt(
    name: str,
    scenario: str,
    system_prompt: str,
    description: str | None = None,
    user_prompt: str | None = None,
    variables: list | None = None,
    is_active: bool = False,
    sort_order: int = 0,
) -> dict:
    """创建新模板。如果 is_active=True，则同一 scenario 下其他模板设为 inactive。"""
    async with async_session() as session:
        # 激活互斥：如果设为激活，先取消同场景其他激活
        if is_active:
            await session.execute(
                update(PromptTemplate)
                .where(PromptTemplate.scenario == scenario, PromptTemplate.is_active == True)  # noqa: E712
                .values(is_active=False)
            )

        pt = PromptTemplate(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            scenario=scenario,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            variables=variables,
            is_active=is_active,
            is_default=False,  # 手动创建的模板永远不是默认模板
            sort_order=sort_order,
        )
        session.add(pt)
        await session.commit()
        await session.refresh(pt)
        logger.info("提示词模板创建成功: id=%s, name=%s, scenario=%s", pt.id, name, scenario)
        return _to_dict(pt)


async def update_prompt(prompt_id: str, **kwargs) -> dict | None:
    """更新模板字段。"""
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        )
        pt = result.scalar_one_or_none()
        if pt is None:
            return None

        # 如果激活状态变化，处理互斥
        if kwargs.get("is_active") and not pt.is_active:
            await session.execute(
                update(PromptTemplate)
                .where(PromptTemplate.scenario == pt.scenario, PromptTemplate.is_active == True)  # noqa: E712
                .values(is_active=False)
            )

        # 更新非 None 字段
        allowed = [
            "name", "description", "scenario", "system_prompt", "user_prompt",
            "variables", "is_active", "sort_order",
        ]
        for field in allowed:
            if field in kwargs and kwargs[field] is not None:
                setattr(pt, field, kwargs[field])

        await session.commit()
        await session.refresh(pt)
        logger.info("提示词模板更新成功: id=%s", prompt_id)
        return _to_dict(pt)


async def delete_prompt(prompt_id: str) -> tuple[bool, str | None]:
    """删除模板。默认模板不可删除。

    Returns:
        (success, error_message)
    """
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        )
        pt = result.scalar_one_or_none()
        if pt is None:
            return False, "模板不存在"
        if pt.is_default:
            return False, "默认模板不可删除"
        await session.delete(pt)
        await session.commit()
        logger.info("提示词模板删除成功: id=%s", prompt_id)
        return True, None


async def set_active(prompt_id: str) -> dict | None:
    """将模板设为当前场景的激活模板（同场景互斥）。"""
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        )
        pt = result.scalar_one_or_none()
        if pt is None:
            return None

        # 同场景所有模板取消激活
        await session.execute(
            update(PromptTemplate)
            .where(PromptTemplate.scenario == pt.scenario, PromptTemplate.is_active == True)  # noqa: E712
            .values(is_active=False)
        )
        # 激活目标模板
        pt.is_active = True
        await session.commit()
        await session.refresh(pt)
        logger.info("提示词模板已激活: id=%s, scenario=%s", prompt_id, pt.scenario)
        return _to_dict(pt)


async def get_active_prompt(scenario: str) -> dict | None:
    """获取指定场景当前激活的模板。"""
    async with async_session() as session:
        result = await session.execute(
            select(PromptTemplate).where(
                PromptTemplate.scenario == scenario,
                PromptTemplate.is_active == True,  # noqa: E712
            )
        )
        pt = result.scalar_one_or_none()
        return _to_dict(pt) if pt else None


async def preview(
    template_id: str | None = None,
    system_prompt: str | None = None,
    variables: dict[str, str] | None = None,
) -> dict:
    """预览渲染结果。

    优先使用 template_id 从 DB 读取模板，否则使用传入的 system_prompt。
    """
    variables = variables or {}

    if template_id:
        template = await get_prompt(template_id)
        if not template:
            return {
                "rendered": "",
                "missing_variables": [],
                "template_variables": None,
                "error": "模板不存在",
            }
        content = template["system_prompt"]
        variable_defs = template.get("variables") or []
    elif system_prompt:
        content = system_prompt
        variable_defs = []
    else:
        return {
            "rendered": "",
            "missing_variables": [],
            "template_variables": None,
            "error": "请提供 template_id 或 system_prompt",
        }

    # 检查缺失的必填变量
    template_vars = extract_variables(content)
    required_vars = {
        vd["name"] for vd in variable_defs if vd.get("required", True)
    } if isinstance(variable_defs, list) else set()
    missing = [v for v in template_vars if v in required_vars and v not in variables]

    # 构建变量定义列表（用于前端展示）
    template_var_list = [
        {"name": v, "label": v, "required": v in required_vars, "description": "模板中声明的变量"}
        for v in template_vars
    ] if not variable_defs else variable_defs

    rendered = render_prompt(content, variables, variable_defs)

    return {
        "rendered": rendered,
        "missing_variables": missing,
        "template_variables": template_var_list,
    }
