"""
智能问答业务逻辑 — RAG 管道 + SSE 流式输出。

Pipeline 阶段:
    1. 获取或创建会话
    2. 加载对话历史
    3. 保存用户消息
    4. RAG 核心（检索 → Prompt 构建 → LLM 生成，见 _run_rag）
    5. 非流式响应按字符切块伪流式输出
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
from core.prompt_renderer import render_prompt, extract_variables
from core.retrieval import retrieve_and_rank
from repositories.conversation_repository import ConversationRepository
from services import prompt_service
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
async def _call_llm(llm, messages: list, model: str) -> str:
    """调用 LLM 并产出 generation span（记录 model + input/output）。"""
    response = await llm.achat(messages=messages)
    content = response.message.content if hasattr(response, "message") else str(response)
    get_client().update_current_generation(model=model, input=messages, output=content)
    return content


@observe(name="rag_qa", capture_input=False, capture_output=False)
async def _run_rag(
    kb_id: str,
    message: str,
    conv_id: str,
    history: list,
) -> tuple[list[dict], str]:
    """RAG 核心：检索 → Prompt 构建 → LLM 生成（非流式）。

    由 @observe 标注，产出 trace "rag_qa"；retrieve/rerank/LLM 各自产出 span。
    session_id 与 trace metadata 经 propagate_attributes 下发到所有子 span。
    返回 (retrieved, full_response)。
    """
    with propagate_attributes(session_id=conv_id, metadata={"kb_id": kb_id}):
        get_client().update_current_span(input=message)

        # 检索（统一链路：召回 + 可选 rerank；span 由 retrieve/rerank 自身产出）
        rerank_config = await resolve_model_config(kb_id, "rerank")  # None = 纯向量
        retrieved = await retrieve_and_rank(
            query=message,
            kb_id=kb_id,
            top_k=settings.retrieval_top_k,
            similarity_threshold=settings.retrieval_similarity_threshold,
            rerank_config=rerank_config,
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

        # LLM 生成（非流式；generation span 记录 input/output/model）
        llm_config = await resolve_model_config(kb_id, "llm")
        llm = create_llm(llm_config)
        full_response = await _call_llm(llm, llm_messages, llm_config["model"])

        get_client().update_current_span(output=full_response)
        return retrieved, full_response


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

        # Stage 4: RAG 核心（检索 + Prompt 构建 + LLM 生成）
        try:
            retrieved, full_response = await _run_rag(kb_id, message, conv_id, history)
        except RuntimeError as e:
            # resolve_model_config 失败（llm/embedding/rerank 未配置）
            logger.error("模型配置解析失败: %s", e, exc_info=True)
            yield _sse_event("error", message=f"模型配置错误: {e}")
            return
        except Exception as llm_err:
            error_msg = f"LLM 调用失败: {llm_err}"
            logger.error(error_msg, exc_info=True)
            yield _sse_event("error", message=error_msg)
            return

        # Stage 5: 非流式响应按 10 字符切块模拟流式输出
        chunk_size = 10
        for i in range(0, len(full_response), chunk_size):
            chunk = full_response[i : i + chunk_size]
            yield _sse_event("token", content=chunk)

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
