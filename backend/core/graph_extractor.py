"""
实体/关系抽取管线 — 词库映射词条/内置正则优先 + LLM 补充 + 合并去重（v2 领域模板动态化）。

流程（docs/10 §3/§7 / docs/plan/48）：
1. 词库映射词条（仅 KB 绑定模板下存在映射的词条才产出，类型 = 映射 code）→ 确定性实体
2. 内置正则（金额/日期，模板含 Quantity/Time 才启用）→ 确定性实体
3. LLM 逐 chunk 补充（提示词由领域模板生成，schema 约束）→ 长尾实体 + 关系
4. 合并去重：全半角/空白归一化，同 (type, name) 合并来源 chunk_ids；
   关系端点/类型按模板过滤（端点不在实体集合丢弃；类型不在模板关系清单丢弃）；
   LLM 实体名仅在“词条在 KB 模板下映射 code == LLM 类型”时归并到标准词

返回结构：
    entities:      [{name, type, description, chunk_ids}]
    relationships: [{source, target, type, description, chunk_ids, a_type, b_type}]
"""
import json
import re
import unicodedata

from langfuse import get_client

from config import settings
from core.model_factory import create_llm, resolve_model_config
from core.observability import extract_usage, traced_span
from models.graph_schemas import ExtractionOutput
from services import domain_service, lexicon_service
from utils.logging import get_logger

logger = get_logger(__name__)

# 内置正则：数量（金额等，第二层匹配，确定性实体不依赖 LLM）
_MONEY_RE = re.compile(
    r"(?:约|共计|合计|为|人民币|RMB)?\s*([\d,，.]+)\s*(万元|亿元|元|美元|欧元|港元|万|亿|吨|kg|公斤|千米|公里|m|平方米|个|人|年|月|天)"
)
_DATE_RE = re.compile(
    r"(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"
)


def _normalize_name(name: str) -> str:
    """实体名归一化：全半角统一 + 去首尾空白。"""
    return unicodedata.normalize("NFKC", (name or "").strip())


def _regex_match_entities(text: str) -> list[dict]:
    """内置正则匹配数量/时间实体（Quantity/Time code）。"""
    entities: list[dict] = []
    for m in _MONEY_RE.finditer(text):
        amount, unit = m.group(1), m.group(2)
        name = f"{amount.replace(',', '').replace('，', '')}{unit}"
        if 0 < len(name) <= 40:
            entities.append({"name": name, "type": "Quantity", "description": "数量/金额"})
    for m in _DATE_RE.finditer(text):
        name = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
        entities.append({"name": name, "type": "Time", "description": "时间"})
    return entities


def _profile_or_default(profile: dict | None) -> dict:
    """None/缺字段 → 内置通用预设兜底（保证链路可用且可生成提示词）。"""
    if profile and profile.get("entity_types") is not None and profile.get("relation_types") is not None:
        return profile
    return domain_service.default_profile()


def _merge_by_code(name: str, etype: str, terms: list[dict]) -> str:
    """LLM 实体名归并（v2 §7.3）：仅当词条在 KB 模板下映射 code == LLM 实体类型时归一为标准词。

    - 精确：name == 标准词 或 name ∈ 别名 → 标准词；
    - 包含：标准词/别名是 name 的子串 → 取最长命中标准词；
    - code 不一致（词条在该域映射为其他类型）不归并，避免跨类型误合并。
    """
    candidates: list[str] = []
    for t in terms:
        if not t.get("term") or t.get("code") != etype:
            continue
        term = t["term"]
        aliases = t.get("aliases") or []
        if name == term or name in aliases:
            return term
        if term in name or any(a and a in name for a in aliases):
            candidates.append(term)
    if candidates:
        return max(candidates, key=len)
    return name


def _assign_chunk_ids(entity_name: str, batch_chunks: list[dict], batch_chunk_ids: list[str]) -> list[str]:
    """chunk 归属（G-06 证据前提，§5.3）：实体名回原文定位，命中哪个 chunk 记入哪个。

    prompt 约束实体 name 与原文一致；此处按子串查找定位实际出现的 chunk；
    定位失败（改名/歧义）降级记入该批第一个 chunk。
    """
    hit_ids = [
        cid for cid, chunk in zip(batch_chunk_ids, batch_chunks)
        if entity_name and entity_name in chunk["text"]
    ]
    if hit_ids:
        return hit_ids
    if batch_chunk_ids:
        return [batch_chunk_ids[0]]
    return []


def _parse_llm_output(text: str) -> ExtractionOutput | None:
    """解析 LLM 输出 JSON → Pydantic 校验；失败返回 None。"""
    raw = (text or "").strip()
    # 剥离可能的 markdown 代码块
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return ExtractionOutput.model_validate(data)
    except Exception as e:
        logger.warning("LLM 抽取输出解析失败: %s", e)
        return None


@traced_span("entity_extract_llm", "generation")
async def _llm_extract_batch(
    kb_id: str, batch_chunks: list[dict], profile: dict | None = None
) -> ExtractionOutput | None:
    """对一批 chunk 调用 LLM 抽取实体/关系（提示词由领域模板生成）。失败重试 1 次。

    每次实际 LLM 调用记录为 langfuse generation（model/input/output/usage）；
    两次尝试均失败/输出解析失败时补记 ERROR 级。
    """
    from llama_index.core.llms import ChatMessage

    llm_config = await resolve_model_config(kb_id, "llm")
    llm = create_llm(llm_config)
    user_text = "\n".join(f"[{c.get('index', i)}] {c['text']}" for i, c in enumerate(batch_chunks))
    # 过长截断，防止超上下文（单次抽取输入上限）
    user_text = user_text[:8000]

    p = _profile_or_default(profile)
    system_prompt = domain_service.build_extraction_prompt(p["entity_types"], p["relation_types"])
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_text),
    ]
    for attempt in range(2):
        try:
            resp = await llm.achat(messages)
            text = resp.message.content or ""
            get_client().update_current_generation(
                model=llm_config.get("model"),
                input=[m.content for m in messages],
                output=text,
                usage_details=extract_usage(resp),
            )
            output = _parse_llm_output(text)
            if output is not None:
                return output
        except Exception as e:
            logger.warning("LLM 抽取调用失败(第 %d 次): %s", attempt + 1, e)
    # 两次尝试均失败或输出解析失败 → 记录错误级 generation，便于 Langfuse 回溯
    get_client().update_current_generation(
        model=llm_config.get("model"),
        input=[m.content for m in messages],
        level="ERROR",
        status_message="LLM 实体抽取失败或输出解析失败",
    )
    return None


@traced_span("entity_extraction", "chain")
async def extract_from_chunks(
    kb_id: str, chunks: list[dict], profile: dict | None = None
) -> dict:
    """对文档切片执行完整抽取管线（每份文档一条 chain，批次 LLM 调用为其子 generation）。

    Args:
        kb_id: 知识库 ID（用于解析 LLM 配置）
        chunks: [{"id": chunk_id, "text": 文本}]（已过滤短 chunk）
        profile: 领域模板紧凑结构（domain_service.get_profile_for_kb 产物）；None → 通用预设兜底

    Returns:
        {
            "entities": [{"name", "type", "description", "chunk_ids"}],
            "relationships": [{"source", "target", "type", "description", "chunk_ids"}],
        }
    """
    await lexicon_service.ensure_cache()
    get_client().update_current_span(input={"kb_id": kb_id, "chunk_count": len(chunks)})

    p = _profile_or_default(profile)
    entity_codes = p["entity_codes"]
    relation_names = p["relation_names"]

    # 词库映射词条：仅 KB 绑定模板下存在映射、且映射 code 在模板内（映射码失效自然过滤）
    lexicon_terms: list[dict] = []
    try:
        lexicon_terms = [
            t for t in await lexicon_service.profile_terms(p.get("id"))
            if t.get("code") in entity_codes
        ]
    except Exception as e:
        logger.warning("词库领域映射加载失败（忽略，仅走正则/LLM）: %s", e)

    # 实体合并桶：key=(type, name) → {name, type, description, chunk_ids}
    entity_bucket: dict[tuple[str, str], dict] = {}
    # 关系桶：key=(source, type, target) → {source, target, type, description, chunk_ids}
    relation_bucket: dict[tuple[str, str, str], dict] = {}

    def _add_entity(name: str, etype: str, description: str, chunk_id: str) -> None:
        n = _normalize_name(name)
        if not n:
            return
        key = (etype, n)
        if key not in entity_bucket:
            entity_bucket[key] = {"name": n, "type": etype, "description": description, "chunk_ids": []}
        bucket = entity_bucket[key]
        if chunk_id not in bucket["chunk_ids"]:
            bucket["chunk_ids"].append(chunk_id)
        if description and not bucket["description"]:
            bucket["description"] = description

    def _add_relation(source: str, target: str, rtype: str, description: str, chunk_id: str) -> None:
        s, t = _normalize_name(source), _normalize_name(target)
        if not s or not t:
            return
        key = (s, rtype, t)
        if key not in relation_bucket:
            relation_bucket[key] = {
                "source": s, "target": t, "type": rtype, "description": description, "chunk_ids": [],
            }
        bucket = relation_bucket[key]
        if chunk_id not in bucket["chunk_ids"]:
            bucket["chunk_ids"].append(chunk_id)
        if description and not bucket["description"]:
            bucket["description"] = description

    # ---------- 1/2. 词库 + 正则匹配（逐 chunk，类型须在模板内） ----------
    llm_batch: list[dict] = []
    llm_batch_chunk_ids: list[str] = []
    batch_size = settings.graph_extraction_batch or 4

    for idx, chunk in enumerate(chunks):
        chunk_id = chunk["id"]
        text = chunk["text"]

        # 词库映射词条（v2 §7：无映射/映射 code 不在模板 → 不进图）
        for word, code in lexicon_service.match_terms(text, lexicon_terms):
            _add_entity(word, code, "", chunk_id)
        # 正则实体（v2：模板含 Quantity/Time 类型才进图）
        for ent in _regex_match_entities(text):
            if ent["type"] in entity_codes:
                _add_entity(ent["name"], ent["type"], ent.get("description", ""), chunk_id)

        # 收集 LLM 批量输入
        llm_batch.append({"index": idx, "text": text})
        llm_batch_chunk_ids.append(chunk_id)

        # 达到批量大小即触发 LLM 抽取
        if len(llm_batch) >= batch_size:
            output = await _llm_extract_batch(kb_id, llm_batch, p)
            if output is not None:
                for e in output.entities:
                    if e.type not in entity_codes:
                        logger.debug("LLM 实体类型不在模板已跳过: %s", e.type)
                        continue
                    # chunk 归属用原文名定位，节点名按“同码词库映射”归并
                    hit_ids = _assign_chunk_ids(e.name, llm_batch, llm_batch_chunk_ids)
                    name = _merge_by_code(e.name, e.type, lexicon_terms)
                    for cid in hit_ids:
                        _add_entity(name, e.type, e.description, cid)
                for r in output.relationships:
                    if r.type not in relation_names:
                        logger.debug("LLM 关系类型不在模板已跳过: %s", r.type)
                        continue
                    for cid in llm_batch_chunk_ids:
                        _add_relation(r.source, r.target, r.type, r.description, cid)
            llm_batch, llm_batch_chunk_ids = [], []

    # 剩余不足一批的 chunk
    if llm_batch:
        output = await _llm_extract_batch(kb_id, llm_batch, p)
        if output is not None:
            for e in output.entities:
                if e.type not in entity_codes:
                    logger.debug("LLM 实体类型不在模板已跳过: %s", e.type)
                    continue
                hit_ids = _assign_chunk_ids(e.name, llm_batch, llm_batch_chunk_ids)
                name = _merge_by_code(e.name, e.type, lexicon_terms)
                for cid in hit_ids:
                    _add_entity(name, e.type, e.description, cid)
            for r in output.relationships:
                if r.type not in relation_names:
                    logger.debug("LLM 关系类型不在模板已跳过: %s", r.type)
                    continue
                for cid in llm_batch_chunk_ids:
                    _add_relation(r.source, r.target, r.type, r.description, cid)

    # ---------- 3. 关系端点过滤：端点不在实体集合中的关系丢弃，并补端点类型 ----------
    entity_type_map: dict[str, str] = {}
    for e in entity_bucket.values():
        entity_type_map.setdefault(e["name"], e["type"])  # 同名多类型取第一个

    relationships: list[dict] = []
    dropped = 0
    for r in relation_bucket.values():
        if r["source"] not in entity_type_map or r["target"] not in entity_type_map:
            dropped += 1
            continue
        r["a_type"] = entity_type_map[r["source"]]
        r["b_type"] = entity_type_map[r["target"]]
        relationships.append(r)
    if dropped:
        logger.debug("关系端点缺失已丢弃: %d 条", dropped)

    logger.info(
        "抽取完成: entities=%d, relationships=%d", len(entity_bucket), len(relationships),
    )
    return {
        "entities": list(entity_bucket.values()),
        "relationships": relationships,
    }
