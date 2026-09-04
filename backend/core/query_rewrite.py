"""
Query 改写 — LLM 将用户输入改写为更利于文档检索的查询语句。

改写只服务召回阶段（dense/sparse/fulltext/graph 与词库扩展使用改写词）；
rerank 相关性判断与最终回答仍使用原问题（由调用方/检索层保证）。

支持两类输出（docs/plan/40）：
- 单意图 → 返回单条改写文本 str（现状）；
- 多意图（用户一次输入多个独立检索意图）→ 返回子查询列表 list[str]，
  由检索层按子查询分别召回并合并结果。

提示词来源：优先提示词管理 `query_rewrite` 场景激活模板（变量 {query}/{history}），
缺失或 DB 异常回退内置常量 FALLBACK_QUERY_REWRITE_PROMPT
（与迁移 0017 seed 的默认模板内容保持一致）。
"""
import json

from langfuse import get_client

from core.model_factory import create_llm
from core.observability import extract_usage, traced_span
from core.prompt_renderer import render_prompt
from services import prompt_service
from utils.logging import get_logger

logger = get_logger(__name__)

# 内置兜底（与 alembic/versions/0017_query_rewrite_default_prompt 的 seed 保持一致）
FALLBACK_QUERY_REWRITE_PROMPT = """你是一名投标文档检索助手。请把下面的检索输入改写成更适合在投标文档库中检索的查询语句：
- 去除口语化表达、寒暄与无关内容；
- 补全行业缩写与关键业务要素（如资质、标准、评分办法等）；
- 保持原意，不要编造原文没有的新要求，不要回答问题；
- 只输出改写后的查询文本本身，不要引号、解释或换行。
{history}检索输入：{query}"""


def _build_history_text(history: list[dict] | None) -> str:
    """把最近几轮对话组装为 {history} 变量内容（无历史返回空串）。"""
    if not history:
        return ""
    lines = [
        f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content')}"
        for m in history
    ]
    return "对话历史（用于理解指代，勿写入结果）：\n" + "\n".join(lines) + "\n\n"


async def _get_rewrite_prompt(query: str, history_text: str = "") -> str:
    """获取改写提示词：优先提示词管理 query_rewrite 激活模板，缺失/异常用内置 fallback。"""
    try:
        active = await prompt_service.get_active_prompt("query_rewrite")
        if active:
            variable_defs = active.get("variables") or []
            return render_prompt(
                active["system_prompt"],
                {"query": query, "history": history_text},
                variable_defs,
            )
    except Exception as e:
        logger.warning("获取 Query 改写提示词失败，使用内置默认值: %s", e)
    return render_prompt(
        FALLBACK_QUERY_REWRITE_PROMPT,
        {"query": query, "history": history_text},
        [],
    )


def _parse_rewrite_output(text: str) -> str | list[str] | None:
    """解析改写/拆解输出 → 单意图 str / 多意图 list[str] / 空 None。

    规则（docs/plan/40 §4.1）：
    - 文本以 '[' 开头 → 整体 json 解析；成功且为字符串数组：
      len >= 2 → list[str]（多意图）；len == 1 → 解包为单条（按单查询清洗）；
      json 解析失败 → 按单行文本处理（回退现状语义）；
    - 其余文本 → 取首行并剥离常见包裹符号（模型可能输出引号/前缀）；空输出返回 None。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                items = [
                    str(i).strip().strip('"\'“”')
                    for i in data
                    if isinstance(i, str) and i.strip()
                ]
                if len(items) >= 2:
                    return items
                if len(items) == 1:
                    return items[0]
        except (ValueError, TypeError):
            pass  # 非合法 JSON → 按单行文本处理
    lines = raw.splitlines()
    return lines[0].strip().strip('"\'“”') if lines else None


@traced_span("query_rewrite", "generation")
async def rewrite_query(
    query: str,
    config: dict,
    history: list[dict] | None = None,
) -> str | list[str]:
    """LLM Query 改写/多意图拆解；任何失败/空输出/结果未变化均静默回退原词。

    每次实际 LLM 调用都记录为 langfuse generation（model/input/output/usage），
    由 traced_span("query_rewrite", "generation") 产出观测。

    Args:
        query: 用户输入的原始检索词/问题
        config: LLM 模型配置 dict（create_llm 入参）
        history: 可选最近对话轮次 [{"role": "user"|"assistant", "content": str}, ...]

    Returns:
        单意图：改写后的查询字符串（str）；
        多意图：子查询列表（list[str]，每个元素一条改写后的子查询）；
        改写不可用/失败/空输出/解析失败时原样返回 query（str）。
    """
    try:
        llm = create_llm(config)
        history_text = _build_history_text(history)
        prompt = await _get_rewrite_prompt(query, history_text)
        resp = await llm.acomplete(prompt)
        parsed = _parse_rewrite_output(resp.text or "")
        if isinstance(parsed, list):
            # 多意图（≥2 条子查询）：直接返回列表，由检索层按子查询分别召回
            get_client().update_current_generation(
                model=config.get("model"),
                input=prompt,
                output=parsed,
                usage_details=extract_usage(resp),
            )
            logger.info("Query 改写(多意图 %d 条): %s", len(parsed), query[:80])
            return parsed
        rewritten = parsed or ""  # 单意图 str / 空输出
        # 先记录后判定：即使输出为空/未变化，本次 LLM 调用也要有 generation 记录
        get_client().update_current_generation(
            model=config.get("model"),
            input=prompt,
            output=rewritten,
            usage_details=extract_usage(resp),
        )
        if not rewritten or rewritten == query:
            return query
        logger.info("Query 改写: %s → %s", query[:80], rewritten[:80])
        return rewritten
    except Exception as e:
        logger.warning("Query 改写失败，回退原词: %s", e)
        get_client().update_current_generation(
            model=config.get("model"),
            input=query,
            level="ERROR",
            status_message=str(e) or type(e).__name__,
        )
        return query
