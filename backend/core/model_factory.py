"""
模型工厂 — 5 类模型 client 工厂 + 运行时模型解析（绑定优先，全局默认兜底）。

工厂函数均为同步、幂等，入参为解析后的模型配置 dict：
    {
        "id", "name", "model_type", "platform", "model",
        "api_base", "api_key", "params", "is_default",
    }

Rerank / Vision 本期仅建模 + 工厂，运行时接入见下期（见 docs/plan/08）。
"""
import os
import re

from sqlalchemy import select

from core.database import async_session
from models.model_config import ModelConfig, KBModelBinding
from utils.logging import get_logger

logger = get_logger(__name__)

MODEL_TYPES = ("llm", "embedding", "rerank", "vision", "ocr")
REQUIRED_MODEL_TYPES = ("llm", "embedding")


def resolve_api_key(api_key: str | None) -> str | None:
    """解析 api_key：支持 ${ENV_VAR} 占位符，否则原样返回。"""
    if not api_key:
        return api_key
    m = re.fullmatch(r"\$\{(\w+)\}", api_key.strip())
    if m:
        return os.environ.get(m.group(1), "")
    return api_key


def _config_to_dict(mc: ModelConfig) -> dict:
    """ORM 对象 → 模型配置 dict。"""
    return {
        "id": str(mc.id),
        "name": mc.name,
        "model_type": mc.model_type,
        "platform": mc.platform,
        "model": mc.model,
        "api_base": mc.api_base,
        "api_key": mc.api_key,
        "params": mc.params or {},
        "is_default": mc.is_default,
    }


async def resolve_model_config(kb_id: str, model_type: str) -> dict | None:
    """解析知识库最终生效的模型配置。

    优先级：kb_model_bindings 绑定 > 全局默认（is_default）。
    - llm / embedding 无默认时抛 RuntimeError（必需）。
    - rerank / vision / ocr 无配置时返回 None（调用方降级/跳过）。
    """
    async with async_session() as session:
        # 1. 绑定优先
        binding_result = await session.execute(
            select(KBModelBinding).where(
                KBModelBinding.kb_id == kb_id,
                KBModelBinding.model_type == model_type,
            )
        )
        binding = binding_result.scalar_one_or_none()
        if binding is not None:
            mc_result = await session.execute(
                select(ModelConfig).where(ModelConfig.id == binding.model_config_id)
            )
            mc = mc_result.scalar_one_or_none()
            if mc is not None:
                logger.debug("resolve 命中绑定: kb_id=%s, type=%s, config=%s", kb_id, model_type, mc.name)
                return _config_to_dict(mc)

        # 2. 全局默认兜底
        default_result = await session.execute(
            select(ModelConfig).where(
                ModelConfig.model_type == model_type,
                ModelConfig.is_default == True,  # noqa: E712
            )
        )
        mc = default_result.scalar_one_or_none()
        if mc is not None:
            logger.debug("resolve 命中默认: type=%s, config=%s", model_type, mc.name)
            return _config_to_dict(mc)

    if model_type in REQUIRED_MODEL_TYPES:
        raise RuntimeError(f"未配置 {model_type} 类型模型（知识库 {kb_id} 无绑定且无全局默认）")
    return None


def create_llm(config: dict):
    """创建语言模型 client（OpenAI 兼容）。"""
    from llama_index.llms.openai_like import OpenAILike

    params = config.get("params") or {}
    logger.info("创建 LLM: platform=%s, model=%s", config.get("platform"), config.get("model"))
    return OpenAILike(
        model=config["model"],
        api_base=config.get("api_base"),
        api_key=resolve_api_key(config.get("api_key")),
        temperature=params.get("temperature", 0.1),
        is_chat_model=True,
    )


def create_embedding(config: dict):
    """创建向量模型 client（OpenAI 兼容）。"""
    from llama_index.embeddings.openai import OpenAIEmbedding

    logger.info("创建 Embedding: model=%s, api_base=%s", config.get("model"), config.get("api_base"))
    return OpenAIEmbedding(
        model_name=config["model"],
        api_base=config.get("api_base"),
        api_key=resolve_api_key(config.get("api_key")),
    )


def create_reranker(config: dict):
    """创建排序模型 client（预留，本期不接检索管线）。"""
    raise NotImplementedError("Rerank 模型运行时接入为下期范围")


def create_vision_llm(config: dict):
    """创建视觉模型 client（预留，本期不接调用点）。"""
    raise NotImplementedError("Vision 模型运行时接入为下期范围")


def create_ocr(config: dict):
    """创建 OCR 客户端。本期仅支持本地 paddle，云后端下期接入。"""
    from paddleocr import PaddleOCR

    params = config.get("params") or {}
    backend = params.get("backend", "paddle")
    lang = params.get("lang", "ch")
    if backend != "paddle":
        raise NotImplementedError(f"OCR backend 未支持: {backend}（本期仅支持本地 paddle）")
    logger.info("创建 OCR: backend=paddle, lang=%s", lang)
    return PaddleOCR(lang=lang, show_log=False)
