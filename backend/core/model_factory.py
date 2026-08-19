"""
模型工厂 — 5 类模型 client 工厂 + 运行时模型解析（绑定优先，全局默认兜底）。

工厂函数均为同步、幂等，入参为解析后的模型配置 dict：
    {
        "id", "name", "model_type", "platform", "model",
        "api_base", "api_key", "params", "is_default",
    }

Rerank 已接入检索重排（OpenAI 兼容 /rerank）；Vision 已支持测试连接（纯文本），运行时调用点仍预留；
OCR 已接入远程 PaddleOCR FastAPI（base64 JSON）。
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
DEFAULT_VISION_PROMPT = (
    "请识别这张投标文档图片中的全部文字和关键信息，并用简洁中文输出结构化文本。"
    "如果包含表格、流程图或架构图，请用文本描述其结构和内容；不要编造不存在的内容。"
)


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

    优先级：kb_model_bindings 绑定（已启用的配置）> 全局默认（is_active 且 is_default）。
    - llm / embedding 无可用配置时抛 RuntimeError（必需）。
    - rerank / vision / ocr 无配置时返回 None（调用方降级/跳过）。
    """
    async with async_session() as session:
        # 1. 绑定优先（绑定指向停用模型时视为未命中，继续走默认）
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
            if mc is not None and mc.is_active:
                logger.debug("resolve 命中绑定: kb_id=%s, type=%s, config=%s", kb_id, model_type, mc.name)
                return _config_to_dict(mc)

        # 2. 全局默认兜底（仅启用模型）
        default_result = await session.execute(
            select(ModelConfig).where(
                ModelConfig.model_type == model_type,
                ModelConfig.is_default == True,  # noqa: E712
                ModelConfig.is_active == True,  # noqa: E712
            )
        )
        mc = default_result.scalar_one_or_none()
        if mc is not None and mc.is_active:
            logger.debug("resolve 命中默认: type=%s, config=%s", model_type, mc.name)
            return _config_to_dict(mc)

    if model_type in REQUIRED_MODEL_TYPES:
        raise RuntimeError(f"未配置 {model_type} 类型模型（知识库 {kb_id} 无绑定且无启用默认）")
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


class OpenAICompatibleReranker:
    """OpenAI 兼容 rerank 端点客户端（Jina / DashScope / SiliconFlow 等约定）。"""

    def __init__(
        self,
        model: str,
        api_base: str,
        api_key: str | None = None,
        endpoint: str = "/rerank",
    ):
        self.model = model
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint  # 路径后缀，如 /rerank（Jina）或 /reranks（DashScope）

    async def arerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """调用 rerank 端点，返回 [(原文档下标, relevance_score)] 列表。"""
        import httpx

        payload: dict = {"model": self.model, "query": query, "documents": documents}
        if top_n:
            payload["top_n"] = top_n
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self.api_base}{self.endpoint}", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        return [(r["index"], r.get("relevance_score", 0.0)) for r in data.get("results", [])]


def create_reranker(config: dict):
    """创建排序模型 client（OpenAI 兼容 rerank 端点，路径后缀可由 params.endpoint 覆盖）。"""
    params = config.get("params") or {}
    logger.info("创建 Reranker: model=%s, api_base=%s", config.get("model"), config.get("api_base"))
    return OpenAICompatibleReranker(
        model=config["model"],
        api_base=config.get("api_base") or "",
        api_key=resolve_api_key(config.get("api_key")),
        endpoint=params.get("endpoint", "/rerank"),
    )


class OpenAICompatibleVisionClient:
    """OpenAI 兼容多模态视觉模型客户端（base64 data URL，同步）。"""

    def __init__(
        self,
        model: str,
        api_base: str,
        api_key: str | None = None,
        endpoint: str = "/chat/completions",
        timeout: float = 60,
        prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.1,
    ):
        self.model = model
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def recognize(
        self,
        image_bytes: bytes,
        content_type: str = "image/png",
        prompt: str | None = None,
    ) -> str:
        """识别图片并返回文本；失败由调用方捕获。"""
        import base64

        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{content_type};base64,{b64}"
        user_prompt = prompt or self.prompt or DEFAULT_VISION_PROMPT
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        payload: dict = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            payload["temperature"] = self.temperature

        resp = self._get_client().post(
            f"{self.api_base}{self.endpoint}",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return self._extract_text(resp.json())

    @staticmethod
    def _extract_text(data) -> str:
        """从 OpenAI 兼容 chat completions 响应提取文本。"""
        if not isinstance(data, dict):
            return ""
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return str(content).strip()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def create_vision_client(config: dict):
    """创建 OpenAI 兼容视觉模型客户端。"""
    params = config.get("params") or {}
    logger.info("创建 Vision Client: platform=%s, model=%s", config.get("platform"), config.get("model"))
    return OpenAICompatibleVisionClient(
        model=config["model"],
        api_base=config.get("api_base") or "",
        api_key=resolve_api_key(config.get("api_key")),
        endpoint=params.get("endpoint", "/chat/completions"),
        timeout=params.get("timeout", 60),
        prompt=params.get("prompt"),
        max_tokens=params.get("max_tokens"),
        temperature=params.get("temperature", 0.1),
    )


def create_vision_llm(config: dict):
    """创建视觉模型 client（OpenAI 兼容多模态；测试连接走纯文本连通性）。"""
    from llama_index.llms.openai_like import OpenAILike

    params = config.get("params") or {}
    logger.info("创建 Vision LLM: platform=%s, model=%s", config.get("platform"), config.get("model"))
    return OpenAILike(
        model=config["model"],
        api_base=config.get("api_base"),
        api_key=resolve_api_key(config.get("api_key")),
        temperature=params.get("temperature", 0.1),
        is_chat_model=True,
    )


class PaddleOCRHttpClient:
    """远程 PaddleOCR FastAPI 服务客户端（同步）。

    支持两种请求协议（由 params.upload_mode 控制，默认 json）：
    - "json"：POST {api_base}{endpoint}，body {"file": "<base64 图片字节>"}
    - "multipart"：POST {api_base}{endpoint}，multipart/form-data 上传字段 file（PP-OCRv6 服务）

    响应：{"text": "...", "rec_texts": [...]}，取 text，回退 rec_texts 拼接。

    同步 httpx：parser 在线程池（asyncio.to_thread）中调用，阻塞线程池线程而非事件循环。
    """

    def __init__(
        self,
        api_base: str,
        api_key: str | None = None,
        endpoint: str = "/ocr",
        timeout: float = 30,
        upload_mode: str = "json",
    ):
        self.api_base = (api_base or "").rstrip("/")
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.upload_mode = upload_mode
        self._client = None  # httpx.Client 懒加载，文档内复用连接

    def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def recognize(self, image_bytes: bytes) -> str:
        """识别图片文字，返回拼接后的文本；失败由调用方捕获（raise_for_status 上抛）。"""
        import base64

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = f"{self.api_base}{self.endpoint}"
        if self.upload_mode == "multipart":
            resp = self._get_client().post(
                url,
                files={"file": ("image.png", image_bytes, "image/png")},
                headers=headers,
            )
        else:
            b64 = base64.b64encode(image_bytes).decode("ascii")
            resp = self._get_client().post(
                url, json={"file": b64}, headers=headers
            )
        resp.raise_for_status()
        return self._extract_text(resp.json())

    @staticmethod
    def _extract_text(data) -> str:
        """从响应提取文字：text 优先，回退 rec_texts 拼接，否则空串。"""
        if not isinstance(data, dict):
            return ""
        text = data.get("text")
        if text:
            return str(text).strip()
        rec_texts = data.get("rec_texts")
        if isinstance(rec_texts, list):
            return " ".join(str(t) for t in rec_texts if t).strip()
        return ""

    def close(self) -> None:
        """关闭底层 httpx.Client（文档解析结束后调用，幂等）。"""
        if self._client is not None:
            self._client.close()
            self._client = None


def create_ocr(config: dict):
    """创建远程 OCR client（PaddleOCR FastAPI）。

    params 支持：
    - endpoint: 路径后缀，默认 "/ocr"（如 PP-OCRv6 服务为 "/ocr/gpu/json"）
    - upload_mode: "json"（默认，base64 JSON）| "multipart"（文件上传，PP-OCRv6）
    """
    params = config.get("params") or {}
    logger.info(
        "创建 OCR: api_base=%s, endpoint=%s, upload_mode=%s",
        config.get("api_base"),
        params.get("endpoint", "/ocr"),
        params.get("upload_mode", "json"),
    )
    return PaddleOCRHttpClient(
        api_base=config.get("api_base") or "",
        api_key=resolve_api_key(config.get("api_key")),
        endpoint=params.get("endpoint", "/ocr"),
        timeout=params.get("timeout", 30),
        upload_mode=params.get("upload_mode", "json"),
    )
