"""
实体/关系抽取管线 — 词库/规则优先 + LLM 补充 + 合并去重。

流程（设计文档 §5.1）：
1. 词库匹配（lexicon_service.match_entities，零成本）→ 高频实体
2. 内置正则（金额/日期）→ 确定性实体
3. LLM 批量补充（graph_extraction_batch 个 chunk 一批）→ 长尾实体 + 关系
4. 合并去重：全半角/空白归一化，同 (type, name) 合并来源 chunk_ids

返回结构：
    entities:      [{name, type, description, chunk_ids}]
    relationships: [{source, target, type, description, chunk_ids}]
"""
import json
import re
import unicodedata

from config import settings
from core.model_factory import create_llm, resolve_model_config
from models.graph_schemas import ExtractionOutput
from services import lexicon_service
from utils.logging import get_logger

logger = get_logger(__name__)

# LLM 实体类型枚举 → 图谱节点标签（通用本体，9 类，docs/09 §3.1）
ENTITY_TYPE_LABEL_MAP = {
    "ORGANIZATION": "Organization",
    "PERSON": "Person",
    "PLACE": "Place",
    "EVENT": "Event",
    "TIME": "Time",
    "PROJECT": "Project",
    "PRODUCT": "Product",
    "QUANTITY": "Quantity",
    "CONCEPT": "Concept",
}

# 内置正则：数量（金额/日期等，第二层匹配，确定性实体不依赖 LLM）
_MONEY_RE = re.compile(
    r"(?:约|共计|合计|为|人民币|RMB)?\s*([\d,，.]+)\s*(万元|亿元|元|美元|欧元|港元|万|亿|吨|kg|公斤|千米|公里|m|平方米|个|人|年|月|天)"
)
_DATE_RE = re.compile(
    r"(\d{4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"
)

_EXTRACTION_SYSTEM_PROMPT = """你是通用信息抽取系统。从文本中提取实体和关系，只提取明确提及的信息，不要编造。
实体 name 必须与原文完全一致（禁止改写、合并或规范化），同一实体在原文中重复出现只需输出一次。

实体类型:
ORGANIZATION(组织/机构/公司/团体), PERSON(人物), PLACE(地点/地理位置), EVENT(事件/活动),
TIME(时间点/日期), PROJECT(项目/工程/任务), PRODUCT(产品/物品/资产/系统),
QUANTITY(数量/金额/度量值,必须含单位,如 3000万元/50吨/2年), CONCEPT(概念/资质/标准/技能/制度/条款)

关系类型(方向见括号,源类型|目标类型):
(Person|Organization)-[属于]->(Organization)
(Organization|Project|Event|Product)-[位于]->(Place)
(Event|Project)-[发生在]->(Time|Place)
(Person|Organization)-[参与]->(Event|Project)
(Person|Organization)-[负责]->(Project|Product|Event)
(Organization|Person)-[合作]->(Organization|Person)
(任意业务实体)-[关联]->(任意业务实体)   ← 语义不明确的通用关系用此类型
(Project|Event|Product|Organization)-[包含]->(Project|Event|Product|Concept|Person)
(Project|Product)-[要求]->(Concept|Product|Person)
(Organization|Person)-[拥有]->(Product|Concept)
(Project|Event|Product|Organization)-[数量]->(Quantity)
(Project|Event|Document)-[日期]->(Time)

输出严格 JSON（不要其他文字，不要 markdown 代码块）:
{"entities":[{"name":"...","type":"ORGANIZATION","description":"..."}],
 "relationships":[{"source":"...","target":"...","type":"属于","description":"..."}]}"""


def _normalize_name(name: str) -> str:
    """实体名归一化：全半角统一 + 去首尾空白。"""
    return unicodedata.normalize("NFKC", (name or "").strip())


def _regex_match_entities(text: str) -> list[dict]:
    """内置正则匹配数量/时间实体（Quantity/Time）。"""
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


def _merge_with_lexicon(name: str, label: str) -> str:
    """词库归并（§5.1/§5.3）：LLM 实体名与词库标准词/别名精确+包含匹配，命中归一为标准词。

    - 精确：name == 标准词 或 name ∈ 别名 → 标准词；
    - 包含：标准词/别名是 name 的子串 → 取最长命中标准词；
    - 仅当词库分类映射的实体类型与 LLM 标签一致才归并（避免跨类型误合并）。
    """
    cache = lexicon_service._cache or {}
    type_map = {
        term: lexicon_service.LEXICON_CATEGORY_ENTITY_TYPES.get(
            lexicon_service.normalize_category(meta["category"])
        )
        for term, meta in cache.items()
    }
    candidates: list[str] = []
    for term, meta in cache.items():
        if type_map.get(term) != label or not term:
            continue
        aliases = meta["aliases"] or []
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


async def _llm_extract_batch(kb_id: str, batch_chunks: list[dict]) -> ExtractionOutput | None:
    """对一批 chunk 调用 LLM 抽取实体/关系。失败重试 1 次。"""
    from llama_index.core.llms import ChatMessage

    llm_config = await resolve_model_config(kb_id, "llm")
    llm = create_llm(llm_config)
    user_text = "\n".join(f"[{c.get('index', i)}] {c['text']}" for i, c in enumerate(batch_chunks))
    # 过长截断，防止超上下文（单次抽取输入上限）
    user_text = user_text[:8000]

    messages = [
        ChatMessage(role="system", content=_EXTRACTION_SYSTEM_PROMPT),
        ChatMessage(role="user", content=user_text),
    ]
    for attempt in range(2):
        try:
            resp = await llm.achat(messages)
            output = _parse_llm_output(resp.message.content)
            if output is not None:
                return output
        except Exception as e:
            logger.warning("LLM 抽取调用失败(第 %d 次): %s", attempt + 1, e)
    return None


async def extract_from_chunks(kb_id: str, chunks: list[dict]) -> dict:
    """对文档切片执行完整抽取管线。

    Args:
        kb_id: 知识库 ID（用于解析 LLM 配置）
        chunks: [{"id": chunk_id, "text": 文本}]（已过滤短 chunk）

    Returns:
        {
            "entities": [{"name", "type", "description", "chunk_ids"}],
            "relationships": [{"source", "target", "type", "description", "chunk_ids"}],
        }
    """
    await lexicon_service.ensure_cache()

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

    # ---------- 1/2. 词库 + 正则匹配（逐 chunk） ----------
    llm_batch: list[dict] = []
    llm_batch_chunk_ids: list[str] = []
    batch_size = settings.graph_extraction_batch or 4

    for idx, chunk in enumerate(chunks):
        chunk_id = chunk["id"]
        text = chunk["text"]

        # 词库实体
        for ent in lexicon_service.match_entities(text):
            _add_entity(ent["name"], ent["type"], "", chunk_id)
        # 正则实体（金额/日期）
        for ent in _regex_match_entities(text):
            _add_entity(ent["name"], ent["type"], ent.get("description", ""), chunk_id)

        # 收集 LLM 批量输入
        llm_batch.append({"index": idx, "text": text})
        llm_batch_chunk_ids.append(chunk_id)

        # 达到批量大小即触发 LLM 抽取
        if len(llm_batch) >= batch_size:
            output = await _llm_extract_batch(kb_id, llm_batch)
            if output is not None:
                for e in output.entities:
                    label = ENTITY_TYPE_LABEL_MAP.get(e.type)
                    if not label:
                        continue
                    # chunk 归属用原文名定位，节点名再词库归并（避免归并后标准词不在原文中）
                    hit_ids = _assign_chunk_ids(e.name, llm_batch, llm_batch_chunk_ids)
                    name = _merge_with_lexicon(e.name, label)
                    for cid in hit_ids:
                        _add_entity(name, label, e.description, cid)
                for r in output.relationships:
                    for cid in llm_batch_chunk_ids:
                        _add_relation(r.source, r.target, r.type, r.description, cid)
            llm_batch, llm_batch_chunk_ids = [], []

    # 剩余不足一批的 chunk
    if llm_batch:
        output = await _llm_extract_batch(kb_id, llm_batch)
        if output is not None:
            for e in output.entities:
                label = ENTITY_TYPE_LABEL_MAP.get(e.type)
                if not label:
                    continue
                hit_ids = _assign_chunk_ids(e.name, llm_batch, llm_batch_chunk_ids)
                name = _merge_with_lexicon(e.name, label)
                for cid in hit_ids:
                    _add_entity(name, label, e.description, cid)
            for r in output.relationships:
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
        "抽取完成: entities=%d, relationships=%d",
        len(entity_bucket), len(relationships),
    )
    return {
        "entities": list(entity_bucket.values()),
        "relationships": relationships,
    }
