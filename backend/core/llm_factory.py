"""
LLM 抽象工厂 — 支持 deepseek / openai / openai_compatible 灵活切换。
"""
from llama_index.llms.openai_like import OpenAILike

from config import Settings
from utils.logging import get_logger

logger = get_logger(__name__)


def create_llm(settings: Settings) -> OpenAILike:
    """根据配置创建 LLM 实例。

    支持 provider:
        - deepseek: DeepSeek 官方 API（默认）
        - openai: OpenAI 官方 API
        - openai_compatible: 任意兼容 OpenAI 接口的模型服务
    """
    logger.info("创建 LLM: provider=%s, model=%s", settings.llm_provider, settings.llm_model)
    return OpenAILike(
        model=settings.llm_model,
        api_base=settings.llm_api_base,
        api_key=settings.llm_api_key,
        temperature=settings.llm_temperature,
        is_chat_model=True,
    )


def create_embedding(settings: Settings):
    """根据配置创建 Embedding 模型实例（Xinference / OpenAI 兼容）。"""
    from llama_index.embeddings.openai import OpenAIEmbedding

    logger.info("创建 Embedding: model=%s, api_base=%s", settings.embedding_model, settings.embedding_api_base)
    return OpenAIEmbedding(
        model_name=settings.embedding_model,
        api_base=settings.embedding_api_base,
        api_key=settings.embedding_api_key,
    )
