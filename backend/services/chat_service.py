"""
智能问答业务逻辑 — RAG 管道 + SSE 流式输出。

Pipeline 阶段:
    1. 获取或创建会话
    2. 检索相关文档片段
    3. 加载对话历史
    4. 构建 RAG Prompt
    5. 保存用户消息
    6. LLM 流式生成
    7. 保存 AI 回复
    8. 发送来源引用
    9. 发送完成信号
"""
import json
from typing import AsyncGenerator

from llama_index.core.base.llms.types import ChatMessage, MessageRole

from config import settings
from core.database import async_session
from core.llm_factory import create_llm
from core.retrieval import retrieve
from repositories.conversation_repository import ConversationRepository
from utils.logging import get_logger

logger = get_logger(__name__)

# RAG Prompt 模板（来自技术设计文档 3.3.2）
RAG_SYSTEM_PROMPT = """你是一个投标文档分析助手。请根据以下文档内容回答用户问题。

【参考文档内容】
{context_str}

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

        # Stage 2: 检索相关文档
        retrieved = await retrieve(query=message, kb_id=kb_id, top_k=settings.retrieval_top_k)
        if not retrieved:
            logger.info("未检索到相关文档: kb_id=%s, query=%s", kb_id, message[:80])

        # Stage 3: 加载对话历史
        async with async_session() as session:
            conv_repo = ConversationRepository(session)
            history = await conv_repo.get_history(conv_id, limit=10)

        # Stage 4: 构建 Prompt
        context_str = _build_context(retrieved) if retrieved else "（未找到相关文档内容）"
        history_str = _build_history(history)
        system_prompt = RAG_SYSTEM_PROMPT.format(context_str=context_str)

        llm_messages = [
            ChatMessage(role=MessageRole.SYSTEM, content=system_prompt),
        ]
        # 添加历史消息作为上下文
        for h in history[-10:]:
            llm_messages.append(ChatMessage(role=h.role, content=h.content))
        # 添加当前用户问题
        llm_messages.append(ChatMessage(role=MessageRole.USER, content=message))

        # Stage 5: 保存用户消息
        async with async_session() as session:
            conv_repo = ConversationRepository(session)
            await conv_repo.create_message(conv_id, role="user", content=message)

        # Stage 6: LLM 流式生成
        llm = create_llm(settings)
        full_response = ""

        try:
            response = await llm.achat(messages=llm_messages)
            full_response = response.message.content if hasattr(response, "message") else str(response)

            # 对于非流式响应，直接返回完整内容
            # 按句子拆分模拟流式输出（实际应使用 astream_chat）
            chunk_size = 10
            for i in range(0, len(full_response), chunk_size):
                chunk = full_response[i : i + chunk_size]
                yield _sse_event("token", content=chunk)

        except Exception as llm_err:
            error_msg = f"LLM 调用失败: {llm_err}"
            logger.error(error_msg, exc_info=True)
            yield _sse_event("error", message=error_msg)
            return

        # Stage 7: 保存 AI 回复
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

        # Stage 8: 发送来源引用
        if retrieved:
            yield _sse_event("sources", sources=_extract_sources(retrieved))

        # Stage 9: 完成
        yield _sse_event("done", session_id=conv_id)

    except Exception as e:
        logger.error("问答管道异常: %s", e, exc_info=True)
        yield _sse_event("error", message=f"服务器内部错误: {e}")
