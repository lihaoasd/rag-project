"""
模型配置业务逻辑 — model_configs CRUD + 默认互斥 + 知识库绑定（含 Embedding 锁定）。
"""
import asyncio
import time
import uuid

from sqlalchemy import delete, select, update

from core.database import async_session
from core.model_factory import create_embedding, create_llm
from models.knowledge_base import KnowledgeBase
from models.model_config import ModelConfig, KBModelBinding
from utils.logging import get_logger

logger = get_logger(__name__)

MODEL_TYPES = ["llm", "embedding", "rerank", "vision", "ocr"]


def _to_dict(mc: ModelConfig) -> dict:
    """ORM 对象 → 响应字典。"""
    return {
        "id": str(mc.id),
        "name": mc.name,
        "model_type": mc.model_type,
        "platform": mc.platform,
        "model": mc.model,
        "api_base": mc.api_base,
        "api_key": mc.api_key,
        "params": mc.params,
        "is_default": mc.is_default,
        "created_at": mc.created_at.isoformat() if mc.created_at else "",
        "updated_at": mc.updated_at.isoformat() if mc.updated_at else "",
    }


async def list_models(model_type: str | None = None, platform: str | None = None) -> list[dict]:
    """模型列表，支持按类型/平台筛选。"""
    async with async_session() as session:
        stmt = select(ModelConfig).order_by(ModelConfig.model_type, ModelConfig.created_at)
        if model_type:
            stmt = stmt.where(ModelConfig.model_type == model_type)
        if platform:
            stmt = stmt.where(ModelConfig.platform == platform)
        result = await session.execute(stmt)
        return [_to_dict(mc) for mc in result.scalars().all()]


async def get_model(model_id: str) -> dict | None:
    """模型详情。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        return _to_dict(mc) if mc else None


async def _clear_defaults(session, model_type: str) -> None:
    """取消同类型下所有默认标记。"""
    await session.execute(
        update(ModelConfig)
        .where(ModelConfig.model_type == model_type, ModelConfig.is_default == True)  # noqa: E712
        .values(is_default=False)
    )


async def create_model(
    name: str,
    model_type: str,
    platform: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
    is_default: bool = False,
) -> dict:
    """创建模型配置。若设默认，先取消同类型其他默认。"""
    async with async_session() as session:
        if is_default:
            await _clear_defaults(session, model_type)
        mc = ModelConfig(
            id=str(uuid.uuid4()),
            name=name,
            model_type=model_type,
            platform=platform,
            model=model,
            api_base=api_base,
            api_key=api_key,
            params=params,
            is_default=is_default,
        )
        session.add(mc)
        await session.commit()
        await session.refresh(mc)
        logger.info("模型配置创建成功: id=%s, name=%s, type=%s", mc.id, name, model_type)
        return _to_dict(mc)


async def update_model(model_id: str, **kwargs) -> dict | None:
    """更新模型配置字段。is_default 由 False→True 时触发互斥。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        if mc is None:
            return None

        if kwargs.get("is_default") and not mc.is_default:
            await _clear_defaults(session, mc.model_type)

        allowed = [
            "name", "model_type", "platform", "model",
            "api_base", "api_key", "params", "is_default",
        ]
        for field in allowed:
            if field in kwargs and kwargs[field] is not None:
                setattr(mc, field, kwargs[field])

        await session.commit()
        await session.refresh(mc)
        logger.info("模型配置更新成功: id=%s", model_id)
        return _to_dict(mc)


async def delete_model(model_id: str) -> tuple[bool, str | None]:
    """删除模型配置。默认模型或被绑定模型不可删除。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        if mc is None:
            return False, "模型配置不存在"
        if mc.is_default:
            return False, "默认模型不可删除，请先取消默认或另设默认"

        # 检查是否被知识库绑定
        bind_result = await session.execute(
            select(KBModelBinding).where(KBModelBinding.model_config_id == model_id).limit(1)
        )
        if bind_result.scalar_one_or_none() is not None:
            return False, "该模型已被知识库绑定，请先解绑"

        await session.delete(mc)
        await session.commit()
        logger.info("模型配置删除成功: id=%s", model_id)
        return True, None


async def set_default(model_id: str) -> dict | None:
    """设为该类型全局默认（同类型互斥）。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        if mc is None:
            return None
        await _clear_defaults(session, mc.model_type)
        mc.is_default = True
        await session.commit()
        await session.refresh(mc)
        logger.info("模型已设为默认: id=%s, type=%s", model_id, mc.model_type)
        return _to_dict(mc)


async def get_kb_models(kb_id: str) -> dict:
    """获取知识库各类型最终生效模型 + 绑定列表。

    Returns:
        {"resolved": [{model_type, model_config_id, config, inherited}], "bindings": [...]}
    """
    async with async_session() as session:
        bindings_result = await session.execute(
            select(KBModelBinding).where(KBModelBinding.kb_id == kb_id)
        )
        bindings = bindings_result.scalars().all()
        binding_map = {b.model_type: b for b in bindings}

        defaults_result = await session.execute(
            select(ModelConfig).where(ModelConfig.is_default == True)  # noqa: E712
        )
        default_map = {mc.model_type: mc for mc in defaults_result.scalars().all()}

    resolved = []
    for mt in MODEL_TYPES:
        binding = binding_map.get(mt)
        if binding is not None:
            config = await get_model(binding.model_config_id)
            resolved.append({
                "model_type": mt,
                "model_config_id": binding.model_config_id,
                "config": config,
                "inherited": False,
            })
        else:
            default_mc = default_map.get(mt)
            resolved.append({
                "model_type": mt,
                "model_config_id": str(default_mc.id) if default_mc else None,
                "config": _to_dict(default_mc) if default_mc else None,
                "inherited": True,
            })

    return {
        "resolved": resolved,
        "bindings": [
            {"model_type": b.model_type, "model_config_id": b.model_config_id, "id": str(b.id)}
            for b in bindings
        ],
    }


async def bind_model(kb_id: str, model_type: str, model_config_id: str | None) -> dict:
    """绑定/解绑知识库模型。model_config_id 为 None 表示解绑回退默认。

    Embedding 模型在知识库已有文档时锁定，禁止切换。
    """
    if model_type not in MODEL_TYPES:
        raise ValueError(f"无效的模型类型: {model_type}")

    async with async_session() as session:
        # 校验知识库存在
        kb_result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = kb_result.scalar_one_or_none()
        if kb is None:
            raise ValueError("知识库不存在")

        # 解绑
        if model_config_id is None:
            if model_type == "embedding" and kb.document_count > 0:
                raise ValueError("该知识库已有文档，Embedding 模型已锁定，禁止切换")
            await session.execute(
                delete(KBModelBinding).where(
                    KBModelBinding.kb_id == kb_id, KBModelBinding.model_type == model_type
                )
            )
            await session.commit()
            logger.info("模型解绑: kb_id=%s, type=%s", kb_id, model_type)
            return {"kb_id": kb_id, "model_type": model_type, "model_config_id": None}

        # 校验模型配置存在且类型匹配
        mc_result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))
        mc = mc_result.scalar_one_or_none()
        if mc is None:
            raise ValueError("模型配置不存在")
        if mc.model_type != model_type:
            raise ValueError(f"模型类型不匹配：期望 {model_type}，实际 {mc.model_type}")

        # Embedding 锁定：KB 有文档时禁止切换
        if model_type == "embedding" and kb.document_count > 0:
            existing = await session.execute(
                select(KBModelBinding).where(
                    KBModelBinding.kb_id == kb_id, KBModelBinding.model_type == "embedding"
                )
            )
            existing_binding = existing.scalar_one_or_none()
            if existing_binding is None or existing_binding.model_config_id != model_config_id:
                raise ValueError("该知识库已有文档，Embedding 模型已锁定，禁止切换")

        # upsert 绑定
        existing = await session.execute(
            select(KBModelBinding).where(
                KBModelBinding.kb_id == kb_id, KBModelBinding.model_type == model_type
            )
        )
        binding = existing.scalar_one_or_none()
        if binding is None:
            binding = KBModelBinding(
                id=str(uuid.uuid4()),
                kb_id=kb_id,
                model_type=model_type,
                model_config_id=model_config_id,
            )
            session.add(binding)
        else:
            binding.model_config_id = model_config_id
        await session.commit()
        logger.info("模型绑定: kb_id=%s, type=%s, config=%s", kb_id, model_type, model_config_id)
        return {"kb_id": kb_id, "model_type": model_type, "model_config_id": model_config_id}


async def delete_kb_bindings(kb_id: str) -> None:
    """删除知识库的所有模型绑定（删除知识库时手动级联）。"""
    async with async_session() as session:
        await session.execute(delete(KBModelBinding).where(KBModelBinding.kb_id == kb_id))
        await session.commit()
        logger.info("知识库模型绑定已删除: kb_id=%s", kb_id)


async def test_model(
    model_type: str,
    platform: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
) -> dict:
    """测试模型连接可用性（不落库）。

    支持 llm / embedding；rerank / vision / ocr 本期仅建模 + 工厂（预留），
    返回不支持在线测试。
    """
    config = {
        "id": "",
        "name": "test",
        "model_type": model_type,
        "platform": platform,
        "model": model,
        "api_base": api_base,
        "api_key": api_key,
        "params": params or {},
        "is_default": False,
    }

    if model_type not in ("llm", "embedding"):
        return {"success": False, "message": "该类型模型本期暂不支持在线测试"}

    start = time.perf_counter()
    try:
        if model_type == "llm":
            from llama_index.core.base.llms.types import ChatMessage, MessageRole

            llm = create_llm(config)
            response = await asyncio.wait_for(
                llm.achat(messages=[ChatMessage(role=MessageRole.USER, content="你好，请回复：OK")]),
                timeout=20,
            )
            content = response.message.content if hasattr(response, "message") else str(response)
            if not content:
                return {"success": False, "message": "模型返回空响应"}
            detail = content[:100]
        else:  # embedding
            emb = create_embedding(config)
            vec = await asyncio.wait_for(emb.aget_text_embedding("测试"), timeout=20)
            if not vec:
                return {"success": False, "message": "向量模型返回空向量"}
            detail = f"向量维度: {len(vec)}"

        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"success": True, "message": "连接成功", "latency_ms": latency_ms, "detail": detail}
    except asyncio.TimeoutError:
        return {"success": False, "message": "连接超时（20 秒）"}
    except Exception as e:
        logger.warning("模型连接测试失败: type=%s, model=%s, error=%s", model_type, model, e)
        return {"success": False, "message": f"测试失败: {e}"}
