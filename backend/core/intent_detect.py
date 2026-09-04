"""问答意图识别 — 判断用户输入是否需要检索投标文档知识库（True=需要检索）还是普通聊天（False）。

提示词来源：优先提示词管理 `intent_detect` 场景激活模板（变量 {query}/{history}），
缺失或 DB 异常回退内置常量 FALLBACK_INTENT_DETECT_PROMPT（与迁移 0020 seed 保持一致）。

兜底策略：识别失败 / 输出无法判定 → 一律返回 True（需要检索）；
LLM 误判为普通聊天但输入为明确资料检索请求（"查询/查一下/帮我查…+主题"）→ 覆盖改判 True，
保证"问答必走检索"行为不回退、不误答。
"""
import json

from langfuse import get_client

from core.model_factory import create_llm
from core.observability import extract_usage, traced_span
from core.prompt_renderer import render_prompt
from services import prompt_service
from utils.logging import get_logger

logger = get_logger(__name__)

# 内置兜底（与 alembic/versions/0020_intent_detect_prompt_v2 的 seed 保持一致）
FALLBACK_INTENT_DETECT_PROMPT = """你是一名问答意图分类器。用户的输入绝大多数是资料检索请求，只有纯社交表达才算"普通聊天"：
- 需要检索：凡是想查询/了解/查看任何主题资料的问题都要检索——无论主题是否熟悉、是否与投标行业相关；纯主题名词的查询（如"查询冠状动脉粥样硬化性心脏病""冠状动脉粥样硬化性心脏病是什么"）也是检索"该主题资料"，需要检索；指代前文检索问题的（如"那这个呢""继续""展开讲讲"）同样需要检索；
- 普通聊天：仅限纯社交表达——问候、感谢、自我介绍/询问助手身份、讲笑话、日常寒暄（如"你好""谢谢""你是谁""讲个笑话""今天天气不错"）；
- 输入不含明显社交信号时一律按"需要检索"处理。
只输出"需要检索"或"普通聊天"中的一个词，不要输出其他内容。
{history}本次输入：{query}"""


def _build_history_text(history: list[dict] | None) -> str:
    """把最近几轮对话组装为 {history} 变量内容（无历史返回空串）。"""
    if not history:
        return ""
    lines = [
        f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content')}"
        for m in history
    ]
    return "对话历史（用于理解指代，勿写入结果）：\n" + "\n".join(lines) + "\n\n"


def _classify_text(text: str) -> bool | None:
    """按关键词把清洗后的文本映射为 True(需要检索)/False(普通聊天)/None(无法判定)。"""
    t = (text or "").strip().lower()
    if not t:
        return None
    # 明确否定检索（如"不需要检索""这无需查文档"）归为普通聊天
    if any(k in t for k in ("不需要检索", "不用检索", "无需检索", "不必检索", "不需要文档", "无需查文档")):
        return False
    if any(k in t for k in ("普通聊天", "闲聊", "寒暄", "small talk", "chat")):
        return False
    if any(k in t for k in ("需要检索", "检索", "文档", "retrieval", "rag")):
        return True
    return None


# 明确的资料检索请求前缀（按长度降序排列；用于 LLM 误判普通聊天时兜底改判）
_SEARCH_REQUEST_PREFIXES = (
    "帮我查一下", "帮我查查", "帮我查找", "帮我搜一下", "帮我查", "帮我找",
    "查询", "查一下", "查一查", "查查", "查找", "搜索", "搜一下", "检索",
    "了解一下", "了解下", "找一下", "查",
)


def _is_information_request(query: str) -> bool:
    """输入是否为明确的资料检索请求（"查询/查一下/帮我查…+主题"形态）。

    覆盖"查询冠状动脉粥样硬化性心脏病"这类主题陌生的检索请求，
    避免模型因主题不在投标示例内而误判为普通聊天；纯寒暄/闲聊不受影响。
    """
    q = (query or "").strip()
    if not q:
        return False
    return any(q.startswith(prefix) for prefix in _SEARCH_REQUEST_PREFIXES)


def _parse_intent_output(text: str) -> bool | None:
    """解析意图识别输出 → True=需要检索 / False=普通聊天 / None=无法判定。

    兼容常见输出：纯文本"需要检索/普通聊天"、英文 retrieval/chat，
    JSON（{"intent": "retrieval"|"chat"}、{"needs_retrieval": true|false}）；空/非法返回 None。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return _classify_text(raw)
        if isinstance(data, dict):
            # 布尔字段优先（needs_retrieval / retrieval_required 等）
            for key in ("needs_retrieval", "retrieval_required", "need_retrieval"):
                value = data.get(key)
                if isinstance(value, bool):
                    return value
            value = data.get("intent", data.get("result", ""))
            if isinstance(value, str):
                return _classify_text(value)
        return None
    return _classify_text(raw)


async def _get_detect_prompt(query: str, history_text: str = "") -> str:
    """获取意图识别提示词：优先提示词管理 intent_detect 激活模板，缺失/异常用内置 fallback。"""
    try:
        active = await prompt_service.get_active_prompt("intent_detect")
        if active:
            variable_defs = active.get("variables") or []
            return render_prompt(
                active["system_prompt"],
                {"query": query, "history": history_text},
                variable_defs,
            )
    except Exception as e:
        logger.warning("获取意图识别提示词失败，使用内置默认值: %s", e)
    return render_prompt(
        FALLBACK_INTENT_DETECT_PROMPT,
        {"query": query, "history": history_text},
        [],
    )


@traced_span("intent_detect", "generation")
async def detect_intent(
    query: str,
    config: dict,
    history: list[dict] | None = None,
) -> bool:
    """LLM 意图识别。

    Args:
        query: 用户本轮输入
        config: LLM 模型配置 dict（create_llm 入参，问答同款解析）
        history: 可选最近几轮对话 [{"role": "user"|"assistant", "content": str}, ...]

    Returns:
        True=需要检索（走 RAG）；False=普通聊天（跳过检索）。
        任何失败/输出无法判定一律返回 True（安全兜底）。
    """
    try:
        llm = create_llm(config)
        history_text = _build_history_text(history)
        prompt = await _get_detect_prompt(query, history_text)
        resp = await llm.acomplete(prompt)
        verdict = _parse_intent_output(resp.text)
        output_text = (resp.text or "").strip()
        # 完整记录本次 LLM 调用（同 query_rewrite/plan 39 口径）：
        # model / 实际输入 prompt / 输出 / token 用量；输出为空或无法判定也保留记录
        get_client().update_current_generation(
            model=config.get("model"),
            input=prompt,
            output=output_text,
            usage_details=extract_usage(resp),
        )
        # LLM 误判普通聊天但输入为明确检索请求时覆盖改判（防"查询疾病名"类主题被当闲聊）
        if verdict is False and _is_information_request(query):
            logger.info("输入为明确检索请求，覆盖识别误判（普通聊天→需要检索）: %s", query[:40])
            return True
        if verdict is not None:
            return verdict
        logger.warning("意图识别输出无法判定，按需检索处理: %r", output_text[:80])
        return True
    except Exception as e:
        logger.warning("意图识别失败，按需检索处理: %s", e)
        get_client().update_current_generation(
            model=config.get("model"),
            input=query,
            level="ERROR",
            status_message=str(e) or type(e).__name__,
        )
        return True
