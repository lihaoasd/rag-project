"""
模型配置业务逻辑 — 模型供应商 CRUD + model_configs CRUD + 默认互斥 + 知识库绑定（含 Embedding 锁定）。
"""
import asyncio
import time
import uuid

from sqlalchemy import delete, func, select, update

from core.database import async_session
from core.model_factory import create_embedding, create_llm, create_reranker, create_vision_llm, resolve_api_key
from models.knowledge_base import KnowledgeBase
from models.model_config import ModelConfig, ModelProvider, KBModelBinding
from utils.logging import get_logger

logger = get_logger(__name__)

MODEL_TYPES = ["llm", "embedding", "rerank", "vision", "ocr"]
# 知识库可绑定类型：rerank 为检索期全局使用，不进知识库绑定
KB_BINDABLE_TYPES = ["llm", "embedding", "vision", "ocr"]


def _provider_to_dict(p: ModelProvider) -> dict:
    """供应商 ORM 对象 → 响应字典。"""
    return {
        "id": str(p.id),
        "name": p.name,
        "platform": p.platform,
        "api_base": p.api_base,
        "api_key": p.api_key,
        "params": p.params,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat() if p.created_at else "",
        "updated_at": p.updated_at.isoformat() if p.updated_at else "",
    }


def _to_dict(mc: ModelConfig) -> dict:
    """ORM 对象 → 响应字典（合并供应商连接信息，前端展示实际生效值）。"""
    provider = getattr(mc, "provider", None)
    provider_active = provider is not None and getattr(provider, "is_active", True)
    api_base, api_key = mc.api_base, mc.api_key
    if provider_active:
        platform = provider.platform or mc.platform
        api_base = mc.api_base or provider.api_base
        api_key = mc.api_key or provider.api_key
        params = {**(provider.params or {}), **(mc.params or {})}
    else:
        platform, params = mc.platform, mc.params
    return {
        "id": str(mc.id),
        "name": mc.name,
        "model_type": mc.model_type,
        "platform": platform,
        "model": mc.model,
        "api_base": api_base,
        "api_key": api_key,
        "params": params,
        "is_default": mc.is_default,
        "is_active": mc.is_active,
        "provider_id": str(provider.id) if provider is not None else None,
        "provider_name": getattr(provider, "name", None) if provider is not None else None,
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


# ============================================================
# 模型供应商 CRUD
# ============================================================
async def list_providers() -> list[dict]:
    """供应商列表（含各供应商下模型数）。"""
    async with async_session() as session:
        result = await session.execute(select(ModelProvider).order_by(ModelProvider.created_at))
        providers = result.scalars().all()
        count_result = await session.execute(
            select(ModelConfig.provider_id, func.count())
            .where(ModelConfig.provider_id.isnot(None))
            .group_by(ModelConfig.provider_id)
        )
        counts = {str(pid): cnt for pid, cnt in count_result.all()}
        out = []
        for p in providers:
            d = _provider_to_dict(p)
            d["model_count"] = counts.get(str(p.id), 0)
            out.append(d)
        return out


async def get_provider(provider_id: str) -> dict | None:
    """供应商详情（含模型数）。"""
    async with async_session() as session:
        result = await session.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
        p = result.scalar_one_or_none()
        if p is None:
            return None
        count_result = await session.execute(
            select(func.count()).where(ModelConfig.provider_id == provider_id)
        )
        d = _provider_to_dict(p)
        d["model_count"] = count_result.scalar_one() or 0
        return d


async def create_provider(
    name: str,
    platform: str,
    api_base: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
    is_active: bool = True,
) -> dict:
    """创建模型供应商（name 唯一）。"""
    async with async_session() as session:
        exist = await session.execute(select(ModelProvider).where(ModelProvider.name == name))
        if exist.scalar_one_or_none() is not None:
            raise ValueError(f"供应商名称已存在: {name}")
        p = ModelProvider(
            id=str(uuid.uuid4()),
            name=name,
            platform=platform,
            api_base=api_base,
            api_key=api_key,
            params=params,
            is_active=is_active,
        )
        session.add(p)
        await session.commit()
        await session.refresh(p)
        logger.info("模型供应商创建成功: id=%s, name=%s", p.id, name)
        return _provider_to_dict(p)


async def update_provider(provider_id: str, **kwargs) -> dict | None:
    """更新模型供应商字段。"""
    async with async_session() as session:
        result = await session.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
        p = result.scalar_one_or_none()
        if p is None:
            return None
        if kwargs.get("name") and kwargs["name"] != p.name:
            exist = await session.execute(
                select(ModelProvider).where(ModelProvider.name == kwargs["name"])
            )
            if exist.scalar_one_or_none() is not None:
                raise ValueError(f"供应商名称已存在: {kwargs['name']}")
        allowed = ["name", "platform", "api_base", "api_key", "params", "is_active"]
        for field in allowed:
            if field in kwargs and kwargs[field] is not None:
                setattr(p, field, kwargs[field])
        await session.commit()
        await session.refresh(p)
        logger.info("模型供应商更新成功: id=%s", provider_id)
        return _provider_to_dict(p)


async def delete_provider(provider_id: str) -> tuple[bool, str | None]:
    """删除供应商。供应商下仍有模型时禁止删除。"""
    async with async_session() as session:
        result = await session.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
        p = result.scalar_one_or_none()
        if p is None:
            return False, "供应商不存在"
        ref = await session.execute(
            select(ModelConfig).where(ModelConfig.provider_id == provider_id).limit(1)
        )
        if ref.scalar_one_or_none() is not None:
            return False, "该供应商下仍有模型，请先移除或改挂其他供应商"
        await session.delete(p)
        await session.commit()
        logger.info("模型供应商删除成功: id=%s", provider_id)
        return True, None


async def fetch_provider_models(provider_id: str) -> dict:
    """从供应商的 OpenAI 兼容 /models 端点拉取可用模型列表（不落库）。

    返回: {"provider_id", "provider_name", "total", "models": [{"id", "owned_by"}]}
    """
    import httpx

    async with async_session() as session:
        result = await session.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
        p = result.scalar_one_or_none()
    if p is None:
        raise ValueError("供应商不存在")

    api_base = (p.api_base or "").strip().rstrip("/")
    if not api_base:
        raise ValueError("该供应商未配置 API Base，无法拉取模型")
    api_key = resolve_api_key(p.api_key)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = f"{api_base}/models"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        raise ValueError("拉取模型超时（15 秒）")
    except httpx.HTTPStatusError as e:
        raise ValueError(f"拉取模型失败: HTTP {e.response.status_code}")
    except Exception as e:
        logger.warning("拉取供应商模型失败: provider=%s, error=%s", p.name, e)
        raise ValueError(f"拉取模型失败: {e}")

    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("供应商返回格式异常：未找到 data 模型数组")

    models: list[dict] = []
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        mid = it.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            models.append({"id": mid, "owned_by": it.get("owned_by", "") or ""})

    return {
        "provider_id": provider_id,
        "provider_name": p.name,
        "total": len(models),
        "models": models,
    }


async def _load_provider(session, provider_id: str | None) -> ModelProvider | None:
    """校验并加载供应商（存在且启用），返回 None 表示未传 provider_id。"""
    if not provider_id:
        return None
    result = await session.execute(select(ModelProvider).where(ModelProvider.id == provider_id))
    p = result.scalar_one_or_none()
    if p is None:
        raise ValueError("供应商不存在")
    if not p.is_active:
        raise ValueError("停用的供应商不能挂载模型，请先启用")
    return p


async def create_model(
    name: str,
    model_type: str,
    platform: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
    is_default: bool = False,
    is_active: bool = True,
    provider_id: str | None = None,
) -> dict:
    """创建模型配置。

    若指定 provider_id：平台为空时继承供应商平台；api_base/api_key 为空则运行时继承供应商，
    模型只存差异覆盖（供应商统一改连接信息对跟随模型生效）。
    若设默认，先取消同类型其他默认。
    """
    async with async_session() as session:
        provider = await _load_provider(session, provider_id)
        if provider is not None and not platform:
            platform = provider.platform
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
            is_active=is_active,
            provider_id=provider_id,
        )
        session.add(mc)
        await session.commit()
        await session.refresh(mc)
        logger.info("模型配置创建成功: id=%s, name=%s, type=%s", mc.id, name, model_type)
        return _to_dict(mc)


async def update_model(model_id: str, **kwargs) -> dict | None:
    """更新模型配置字段。is_default 由 False→True 时触发互斥；默认模型停用时同步取消默认。

    更新 provider_id 时：平台未显式指定则继承新供应商平台；停用供应商不可挂载模型。
    """
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        if mc is None:
            return None

        if kwargs.get("is_default") and not mc.is_default:
            await _clear_defaults(session, mc.model_type)

        allowed = [
            "name", "model_type", "platform", "model",
            "api_base", "api_key", "params", "is_default", "is_active", "provider_id",
        ]
        for field in allowed:
            if field in kwargs and kwargs[field] is not None:
                setattr(mc, field, kwargs[field])

        # 显式解绑供应商（provider_id=null）
        if "provider_id" in kwargs and kwargs["provider_id"] is None:
            mc.provider_id = None

        # 供应商变更：校验存在且启用；平台未显式指定则继承新供应商平台
        if kwargs.get("provider_id"):
            provider = await _load_provider(session, kwargs["provider_id"])
            if provider is not None and not kwargs.get("platform"):
                mc.platform = provider.platform

        # 默认模型被停用时取消默认，避免出现不可用的默认
        if kwargs.get("is_active") is False and mc.is_default:
            mc.is_default = False

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
    """设为该类型全局默认（同类型互斥，仅限启用模型）。"""
    async with async_session() as session:
        result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_id))
        mc = result.scalar_one_or_none()
        if mc is None:
            return None
        if not mc.is_active:
            raise ValueError("停用的模型不能设为默认，请先启用")
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
    for mt in KB_BINDABLE_TYPES:
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
    if model_type not in KB_BINDABLE_TYPES:
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
                # 仅在确实存在绑定时才视为「切换」；已跟随全局默认时解绑是 no-op
                existing = await session.execute(
                    select(KBModelBinding).where(
                        KBModelBinding.kb_id == kb_id, KBModelBinding.model_type == "embedding"
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    raise ValueError("该知识库已有文档，Embedding 模型已锁定，禁止切换")
            await session.execute(
                delete(KBModelBinding).where(
                    KBModelBinding.kb_id == kb_id, KBModelBinding.model_type == model_type
                )
            )
            await session.commit()
            logger.info("模型解绑: kb_id=%s, type=%s", kb_id, model_type)
            return {"kb_id": kb_id, "model_type": model_type, "model_config_id": None}

        # 校验模型配置存在、类型匹配且已启用
        mc_result = await session.execute(select(ModelConfig).where(ModelConfig.id == model_config_id))
        mc = mc_result.scalar_one_or_none()
        if mc is None:
            raise ValueError("模型配置不存在")
        if mc.model_type != model_type:
            raise ValueError(f"模型类型不匹配：期望 {model_type}，实际 {mc.model_type}")
        if not mc.is_active:
            raise ValueError("停用的模型不能绑定，请先启用")

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


async def _test_ocr_connectivity(config: dict) -> dict:
    """远程 OCR 连通性探测：GET api_base 根路径，任意 HTTP 响应视为连通。"""
    import httpx

    api_base = (config.get("api_base") or "").strip().rstrip("/")
    if not api_base:
        return {"success": False, "message": "未配置 API 地址，无法测试远程 OCR 连通性"}
    api_key = resolve_api_key(config.get("api_key"))
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(api_base, headers=headers)
        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"success": True, "message": "连接成功", "latency_ms": latency_ms, "detail": f"HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        return {"success": False, "message": "连接超时（10 秒）"}
    except httpx.ConnectError as e:
        return {"success": False, "message": f"连接失败: {e}"}
    except Exception as e:
        return {"success": False, "message": f"测试失败: {e}"}


async def test_model(
    model_type: str,
    platform: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    params: dict | None = None,
) -> dict:
    """测试模型连接可用性（不落库）。

    支持 llm / embedding / rerank / vision / ocr；vision 走纯文本连通性（复用 llm 测试），
    ocr 走远程连通性探测（GET api_base 根路径，任意 HTTP 响应视为连通）。
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

    if model_type not in ("llm", "embedding", "rerank", "vision", "ocr"):
        return {"success": False, "message": "该类型模型本期暂不支持在线测试"}

    start = time.perf_counter()
    try:
        if model_type in ("llm", "vision"):
            from llama_index.core.base.llms.types import ChatMessage, MessageRole

            llm = create_llm(config) if model_type == "llm" else create_vision_llm(config)
            response = await asyncio.wait_for(
                llm.achat(messages=[ChatMessage(role=MessageRole.USER, content="你好，请回复：OK")]),
                timeout=20,
            )
            content = response.message.content if hasattr(response, "message") else str(response)
            if not content:
                return {"success": False, "message": "模型返回空响应"}
            detail = content[:100]
        elif model_type == "embedding":
            emb = create_embedding(config)
            vec = await asyncio.wait_for(emb.aget_text_embedding("测试"), timeout=20)
            if not vec:
                return {"success": False, "message": "向量模型返回空向量"}
            detail = f"向量维度: {len(vec)}"
        elif model_type == "rerank":
            reranker = create_reranker(config)
            results = await asyncio.wait_for(
                reranker.arerank("测试查询", ["投标文件内容示例", "另一段示例文本"], top_n=2),
                timeout=20,
            )
            if not results:
                return {"success": False, "message": "排序模型返回空结果"}
            detail = f"返回 {len(results)} 条，最高分 {results[0][1]:.4f}"
        elif model_type == "ocr":
            return await _test_ocr_connectivity(config)

        latency_ms = round((time.perf_counter() - start) * 1000)
        return {"success": True, "message": "连接成功", "latency_ms": latency_ms, "detail": detail}
    except asyncio.TimeoutError:
        return {"success": False, "message": "连接超时（20 秒）"}
    except Exception as e:
        logger.warning("模型连接测试失败: type=%s, model=%s, error=%s", model_type, model, e)
        return {"success": False, "message": f"测试失败: {e}"}


async def startup_model_check(timeout: float = 15.0) -> list[dict]:
    """启动时对启用状态的模型做连接自检（并发，总超时；失败仅记日志不阻断启动）。

    Returns:
        [{model_id, name, model_type, model, success, message, latency_ms}]
    """
    async with async_session() as session:
        result = await session.execute(
            select(ModelConfig).where(ModelConfig.is_active == True)  # noqa: E712
        )
        configs = result.scalars().all()

    if not configs:
        logger.info("启动自检: 无启用状态的模型")
        return []

    async def _check_one(mc: ModelConfig) -> dict:
        start = time.perf_counter()
        try:
            merged = _to_dict(mc)  # 合并供应商连接信息（模型继承供应商后 api_base/api_key 在供应商侧）
            res = await test_model(
                model_type=merged["model_type"],
                platform=merged["platform"],
                model=merged["model"],
                api_base=merged["api_base"],
                api_key=merged["api_key"],
                params=merged["params"],
            )
            latency_ms = round((time.perf_counter() - start) * 1000)
            return {
                "model_id": str(mc.id),
                "name": mc.name,
                "model_type": mc.model_type,
                "model": mc.model,
                "success": bool(res.get("success")),
                "message": res.get("message", ""),
                "latency_ms": latency_ms,
            }
        except Exception as e:
            logger.warning("模型自检异常: name=%s, error=%s", mc.name, e)
            return {
                "model_id": str(mc.id),
                "name": mc.name,
                "model_type": mc.model_type,
                "model": mc.model,
                "success": False,
                "message": f"自检异常: {e}",
                "latency_ms": None,
            }

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*[_check_one(mc) for mc in configs]), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("启动自检超时（%.0fs），部分模型未完成检查", timeout)
        results = []

    ok = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    logger.info(
        "启动自检完成: 共 %d 个启用模型，成功 %d，失败 %d",
        len(configs), len(ok), len(failed),
    )
    for r in failed:
        logger.warning(
            "模型自检失败: name=%s, type=%s, model=%s, msg=%s",
            r["name"], r["model_type"], r["model"], r["message"],
        )
    return results
