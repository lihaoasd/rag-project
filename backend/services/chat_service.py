"""
智能问答业务逻辑 — RAG 管道 + SSE 流式输出。

Pipeline 阶段:
    1. 获取或创建会话
    2. 加载对话历史
    3. 保存用户消息
    4. RAG 核心（检索 → Prompt 构建 → LLM 流式生成，见 _run_rag）
    5. 流式逐 token 输出
    6. 保存 AI 回复
    7. 发送来源引用
    8. 发送完成信号
"""
import json
from typing import AsyncGenerator

from langfuse import get_client, observe, propagate_attributes
from llama_index.core.base.llms.types import ChatMessage, MessageRole

from config import settings
from core.database import async_session
from core.model_factory import create_llm, resolve_model_config
from core.observability import traced_span
from core.prompt_renderer import render_prompt, extract_variables
from core.retrieval import retrieve_and_rank
from repositories.conversation_repository import ConversationRepository
from services import lexicon_service, prompt_service
from utils.logging import get_logger

logger = get_logger(__name__)

# Fallback Prompt（数据库无模板时使用，与迁移默认数据保持一致）
FALLBACK_RAG_PROMPT = """你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

【对话历史】
{history}

【用户问题】
{query}

请基于参考文档内容回答。如果文档中找不到相关信息，请明确说明。
回答时请注明信息来源（文档名、页码）。"""


def _build_context(retrieved: list[dict]) -> str:
    """将检索结果格式化为 Prompt 上下文。"""
    parts = []
    for i, r in enumerate(retrieved, start=1):
        source = f"[{i}] 来源：{r['file_name']} 第{r['page_number']}页"
        parts.append(f"{source}\n{r['text']}")
    return "\n\n---\n\n".join(parts)


def _build_history(messages: list) -> str:
    """将历史消息格式化为对话历史文本。"""
    if not messages:
        return "（无历史对话）"
    lines = []
    for msg in messages:
        role = "用户" if msg.role == "user" else "AI助手"
        lines.append(f"{role}: {msg.content}")
    return "\n".join(lines)


def _sse_event(event_type: str, **kwargs) -> str:
    """构建 SSE 事件行。"""
    data = {"type": event_type, **kwargs}
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _extract_sources(retrieved: list[dict]) -> list[dict]:
    """从检索结果中提取来源引用信息。"""
    return [
        {
            "doc_id": r["doc_id"],
            "file_name": r["file_name"],
            "page_number": r["page_number"],
            "chunk_index": r.get("chunk_index", 0),
            "chunk_type": r.get("chunk_type", "text"),
            "source_chunk_index": r.get("source_chunk_index", -1),
            "score": r["score"],
            "text": r["text"][:500],  # 截断长文本
        }
        for r in retrieved
    ]


async def _get_system_prompt(
    context_str: str,
    query: str,
    history_str: str,
    kb_name: str = "",
) -> str:
    """获取并渲染 RAG 系统提示词。

    优先从 DB 读取激活模板，不存在则使用内置 fallback。
    """
    try:
        active = await prompt_service.get_active_prompt("rag_qa")
        if active:
            variables = {
                "context_str": context_str,
                "query": query,
                "history": history_str,
                "kb_name": kb_name,
            }
            variable_defs = active.get("variables") or []
            return render_prompt(active["system_prompt"], variables, variable_defs)
    except Exception as e:
        logger.warning("获取动态 Prompt 失败，使用 fallback: %s", e)

    # Fallback
    return FALLBACK_RAG_PROMPT.format(
        context_str=context_str,
        query=query,
        history=history_str,
        kb_name=kb_name,
    )


@observe(as_type="generation", capture_input=False, capture_output=False)
async def _call_llm_stream(llm, messages: list, model: str) -> AsyncGenerator[str, None]:
    """流式调用 LLM 并产出 generation span（逐 token yield，结束记录 model/output/usage；失败也记录 error 后向上抛）。"""
    try:
        full: list[str] = []
        usage: dict = {}
        stream = await llm.astream_chat(
            messages=messages, stream_options={"include_usage": True}
        )
        async for resp in stream:
            delta = resp.delta or ""
            if delta:
                full.append(delta)
                yield delta
            # 最后一个 chunk 携带 token 用量（additional_kwargs 内含 prompt/completion/total_tokens）
            ak = resp.additional_kwargs or {}
            if "prompt_tokens" in ak:
                usage = {
                    "prompt_tokens": ak["prompt_tokens"],
                    "completion_tokens": ak["completion_tokens"],
                    "total_tokens": ak["total_tokens"],
                }
        get_client().update_current_generation(
            model=model,
            input=messages,
            output="".join(full),
            usage_details=usage or None,
        )
    except Exception as e:
        # 失败也写入 model/input 与错误信息，保证失败的一次生成在 Langfuse 中可追溯
        get_client().update_current_generation(
            model=model,
            input=messages,
            level="ERROR",
            status_message=str(e) or type(e).__name__,
        )
        raise


@traced_span("rag_qa", "CHAIN")
async def _run_rag(
    kb_id: str,
    message: str,
    conv_id: str,
    history: list,
    result: dict | None = None,
) -> AsyncGenerator[str, None]:
    """RAG 核心：检索 → Prompt 构建 → LLM 流式生成（逐 token yield）。

    由 traced_span 标注，产出 trace "rag_qa"（CHAIN）；retrieve/rerank/LLM 各自产出 span。
    session_id 与 trace metadata 经 propagate_attributes 下发到所有子 span。
    retrieved 经 result dict 传回（async generator 无法用 return 传值）。
    """
    with propagate_attributes(session_id=conv_id, metadata={"kb_id": kb_id}):
        get_client().update_current_span(input=message)

        # 检索（三路混合：dense/sparse/fulltext 召回 + RRF 融合 + 可选 rerank；span 由 retrieve/rerank 自身产出）
        rerank_config = await resolve_model_config(kb_id, "rerank")  # None = 无重排
        try:
            await lexicon_service.ensure_cache()  # fulltext 路词库查询扩展缓存（失败不阻断问答）
        except Exception as e:
            logger.warning("词库缓存加载失败，问答跳过查询扩展: %s", e)
        retrieved = await retrieve_and_rank(
            query=message,
            kb_id=kb_id,
            top_k=settings.retrieval_top_k,
            similarity_threshold=settings.retrieval_similarity_threshold,
            rerank_config=rerank_config,
            rerank_threshold=settings.retrieval_rerank_threshold,
            retrieval_methods=settings.chat_retrieval_methods,
            expand_query=lexicon_service.expand_query,
        )
        if not retrieved:
            logger.info("未检索到相关文档: kb_id=%s, query=%s", kb_id, message[:80])

        # Prompt 构建（从 DB 动态获取）
        context_str = _build_context(retrieved) if retrieved else "（未找到相关文档内容）"
        history_str = _build_history(history)
        system_prompt = await _get_system_prompt(
            context_str=context_str,
            query=message,
            history_str=history_str,
            kb_name=kb_id,  # 后续可替换为真实知识库名
        )
        llm_messages = [ChatMessage(role=MessageRole.SYSTEM, content=system_prompt)]
        for h in history[-10:]:
            llm_messages.append(ChatMessage(role=h.role, content=h.content))
        llm_messages.append(ChatMessage(role=MessageRole.USER, content=message))

        # LLM 流式生成（generation span 记录 input/output/model）
        llm_config = await resolve_model_config(kb_id, "llm")
        llm = create_llm(llm_config)
        full: list[str] = []
        async for delta in _call_llm_stream(llm, llm_messages, llm_config["model"]):
            full.append(delta)
            yield delta

        content = "".join(full)
        get_client().update_current_span(output=content)
        if result is not None:
            result["retrieved"] = retrieved


async def chat_stream(
    kb_id: str,
    message: str,
    session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """SSE 流式问答 — RAG 管道。

    Args:
        kb_id: 目标知识库 ID
        message: 用户问题
        session_id: 会话 ID（可选，不传则创建新会话）

    Yields:
        SSE 事件字符串
    """
    conv_id = session_id  # 追踪会话 ID 用于响应

    try:
        # Stage 1: 获取或创建会话
        async with async_session() as session:
            conv_repo = ConversationRepository(session)
            conv = await conv_repo.get_or_create(session_id, kb_id)
            conv_id = str(conv.id)
            logger.info("会话就绪: conv_id=%s, kb_id=%s", conv_id, kb_id)

        # Stage 2: 加载对话历史
        async with async_session() as session:
            conv_repo = ConversationRepository(session)
            history = await conv_repo.get_history(conv_id, limit=10)

        # Stage 3: 保存用户消息
        async with async_session() as session:
            conv_repo = ConversationRepository(session)
            await conv_repo.create_message(conv_id, role="user", content=message)

        # Stage 4: RAG 核心（检索 + Prompt 构建 + LLM 流式生成）
        result: dict = {}
        full_parts: list[str] = []
        try:
            async for delta in _run_rag(kb_id, message, conv_id, history, result):
                full_parts.append(delta)
                yield _sse_event("token", content=delta)
        except RuntimeError as e:
            # resolve_model_config 失败（llm/embedding/rerank 未配置）
            logger.error("模型配置解析失败: %s", e, exc_info=True)
            get_client().flush()
            yield _sse_event("error", message=f"模型配置错误: {e}")
            return
        except Exception as llm_err:
            error_msg = f"LLM 调用失败: {llm_err}"
            logger.error(error_msg, exc_info=True)
            get_client().flush()
            yield _sse_event("error", message=error_msg)
            return

        # Stage 5: 流式已完成，组装完整回答
        retrieved = result.get("retrieved")
        full_response = "".join(full_parts)

        # Stage 6: 保存 AI 回复
        try:
            sources_data = _extract_sources(retrieved) if retrieved else None
            async with async_session() as session:
                conv_repo = ConversationRepository(session)
                await conv_repo.create_message(
                    conv_id,
                    role="assistant",
                    content=full_response,
                    sources=sources_data,
                )
        except Exception as db_err:
            logger.error("保存 AI 回复失败: %s", db_err, exc_info=True)

        # Stage 7: 发送来源引用
        if retrieved:
            yield _sse_event("sources", sources=_extract_sources(retrieved))

        # Stage 8: 完成
        yield _sse_event("done", session_id=conv_id)

    except Exception as e:
        logger.error("问答管道异常: %s", e, exc_info=True)
        yield _sse_event("error", message=f"服务器内部错误: {e}")
