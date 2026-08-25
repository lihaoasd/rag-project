"""
图谱业务逻辑 — 构建 / 清除 / 查询编排 + graph_status 落库 + chunk 级证据。

设计文档：docs/09-graph-module-design.md

关键约定：
- 图谱本体在 Neo4j（Document 文件锚点 + Entity 节点 + 业务关系）；
- 实体节点属性 mentioned_chunk_ids 记录来源切片，证据检索凭 chunk_id
  实时查询 document_chunks（零冗余）；
- 关系来源按文件级 source_doc_ids 追踪；
- 关系类型白名单校验（来自 LLM 自由文本，防 Cypher 注入）。
"""
import asyncio
import time

from config import settings
from core.database import async_session
from core.graph_extractor import ENTITY_TYPE_LABEL_MAP, _regex_match_entities, extract_from_chunks
from core.neo4j_store import GraphDisabledError, Neo4jStore
from core import graph_queue
from repositories.document_repository import DocumentRepository
from services import lexicon_service
from utils.logging import get_logger

logger = get_logger(__name__)

# 业务关系类型白名单（通用本体 12 种，docs/09 §5.3；白名单外关系丢弃）
RELATION_TYPES = {
    "属于", "位于", "发生在", "参与", "负责", "合作",
    "关联", "包含", "要求", "拥有", "数量", "日期",
}

# 实体类型标签白名单（写图标签拼接用）
_ENTITY_LABELS = set(ENTITY_TYPE_LABEL_MAP.values())


def _ensure_graph_enabled() -> None:
    """图谱未启用/Neo4j 不可达时抛 GraphDisabledError（router 转 503）。"""
    if not settings.graph_enabled:
        raise GraphDisabledError("图谱功能未启用（GRAPH_ENABLED=false）")


# ============================================================
# 写入（MERGE 幂等，单事务）
# ============================================================

def _build_write_statements(
    kb_id: str,
    doc_id: str,
    file_name: str,
    entities: list[dict],
    relationships: list[dict],
) -> list[tuple[str, dict]]:
    """构造单文件图谱写入的 Cypher 语句列表（一个事务执行）。

    - 实体节点：键 = (kb_id, name, type 属性)；合并 mentioned_chunk_ids
    - 业务关系：两端已存在时 MERGE，来源按 source_doc_ids（白名单类型）
    - Document 锚点 + 提及 关系
    """
    statements: list[tuple[str, dict]] = []

    for ent in entities:
        label = ent.get("type", "")
        if label not in _ENTITY_LABELS:
            logger.debug("实体类型非法已跳过: %s", label)
            continue
        chunk_ids = ent.get("chunk_ids") or []
        statements.append((
            "MERGE (n:Entity {kb_id: $kb_id, name: $name, type: $type}) "
            f"ON CREATE SET n:{label}, n.created_at = datetime(), "
            "n.mentioned_chunk_ids = $chunk_ids, n.description = $description "
            # description 先到先得：ON MATCH 不覆盖（§5.1/§4.4）
            "ON MATCH SET n.mentioned_chunk_ids = reduce(acc = n.mentioned_chunk_ids, "
            "c IN $chunk_ids | CASE WHEN c IN acc THEN acc ELSE acc + c END)",
            {
                "kb_id": kb_id, "name": ent["name"], "type": label,
                "chunk_ids": chunk_ids, "description": ent.get("description", ""),
            },
        ))

    for rel in relationships:
        rtype = rel.get("type", "")
        if rtype not in RELATION_TYPES:
            logger.debug("关系类型不在白名单已跳过: %s", rtype)
            continue
        statements.append((
            "MATCH (a:Entity) WHERE a.kb_id = $kb_id AND a.name = $a_name AND a.type = $a_type "
            "MATCH (b:Entity) WHERE b.kb_id = $kb_id AND b.name = $b_name AND b.type = $b_type "
            f"MERGE (a)-[r:`{rtype}` {{kb_id: $kb_id}}]->(b) "
            "ON CREATE SET r.type = $rtype, r.source_doc_ids = [$doc_id], "
            "r.description = $description "
            "ON MATCH SET r.source_doc_ids = reduce(acc = r.source_doc_ids, "
            "d IN [$doc_id] | CASE WHEN d IN acc THEN acc ELSE acc + d END)",
            {
                "kb_id": kb_id,
                "a_name": rel["source"], "a_type": rel.get("a_type", "Event"),
                "b_name": rel["target"], "b_type": rel.get("b_type", "Event"),
                "rtype": rtype, "doc_id": doc_id, "description": rel.get("description", ""),
            },
        ))

    # Document 锚点
    statements.append((
        "MERGE (d:Document {kb_id: $kb_id, doc_id: $doc_id}) "
        "ON CREATE SET d.file_name = $file_name, d.created_at = datetime()",
        {"kb_id": kb_id, "doc_id": doc_id, "file_name": file_name},
    ))
    # 提及 关系（锚点 → 实体）
    for ent in entities:
        label = ent.get("type", "")
        if label not in _ENTITY_LABELS:
            continue
        statements.append((
            "MATCH (d:Document) WHERE d.kb_id = $kb_id AND d.doc_id = $doc_id "
            "MATCH (e:Entity) WHERE e.kb_id = $kb_id AND e.name = $name AND e.type = $type "
            "MERGE (d)-[:`提及` {kb_id: $kb_id}]->(e)",
            {"kb_id": kb_id, "doc_id": doc_id, "name": ent["name"], "type": label},
        ))

    return statements


# 图谱写入分批事务：每条事务最多包含的语句数（约 20~50 个 chunk 产生的写入，§4.4）
GRAPH_WRITE_BATCH_STATEMENTS = 100


async def _write_in_batches(statements: list[tuple[str, dict]]) -> None:
    """按批执行写入语句（每批一个事务），批间失败可重试（MERGE 幂等）。"""
    for i in range(0, len(statements), GRAPH_WRITE_BATCH_STATEMENTS):
        batch = statements[i : i + GRAPH_WRITE_BATCH_STATEMENTS]
        await Neo4jStore.execute_write_many(batch)


# ============================================================
# 清除
# ============================================================

async def _clear_file_contributions(kb_id: str, doc_id: str) -> None:
    """清除单文件对图谱的全部贡献（供构建前清旧 / 清除操作复用）。

    1. PG 查该文件全部 chunk_id
    2. Neo4j 三步：删 Document 锚点+提及 → 剥离实体 mentioned_chunk_ids（空即删）
       → 剥离关系 source_doc_ids（空即删）
    """
    # PG：取该文件全部切片 ID
    async with async_session() as session:
        repo = DocumentRepository(session)
        chunks = await repo.list_chunks_for_graph(doc_id)
    chunk_ids = [str(c.id) for c in chunks]

    statements: list[tuple[str, dict]] = [
        (
            "MATCH (d:Document) WHERE d.doc_id = $doc_id AND d.kb_id = $kb_id "
            "OPTIONAL MATCH (d)-[m:`提及`]->() DELETE m, d",
            {"doc_id": doc_id, "kb_id": kb_id},
        ),
        (
            "MATCH (n:Entity) WHERE n.kb_id = $kb_id "
            "AND any(c IN $chunk_ids WHERE c IN n.mentioned_chunk_ids) "
            "WITH n, [c IN n.mentioned_chunk_ids WHERE NOT c IN $chunk_ids] AS rest "
            "SET n.mentioned_chunk_ids = rest "
            "WITH n, rest WHERE size(rest) = 0 DETACH DELETE n",
            {"kb_id": kb_id, "chunk_ids": chunk_ids},
        ),
        (
            "MATCH (a)-[r]->(b) "
            "WHERE r.kb_id = $kb_id AND $doc_id IN r.source_doc_ids "
            "WITH r, [x IN r.source_doc_ids WHERE x <> $doc_id] AS rest "
            "SET r.source_doc_ids = rest "
            "WITH r, rest WHERE size(rest) = 0 DELETE r",
            {"kb_id": kb_id, "doc_id": doc_id},
        ),
    ]
    await Neo4jStore.execute_write_many(statements)
    logger.info("单文件图谱贡献已清除: kb_id=%s, doc_id=%s, chunks=%d", kb_id, doc_id, len(chunk_ids))


# ============================================================
# 构建管理（G-03 / G-04 / G-05）
# ============================================================

async def build_file_graph(kb_id: str, doc_id: str) -> None:
    """单文件增量构建 — 校验 → 清旧 → 抽取 → 写图 → 状态落库。"""
    _ensure_graph_enabled()
    started = time.perf_counter()
    try:
        async with async_session() as session:
            repo = DocumentRepository(session)
            doc = await repo.get_by_id(doc_id)
            if doc is None:
                logger.error("图谱构建失败：文档不存在 doc_id=%s", doc_id)
                return
            if doc.status != "completed":
                logger.warning("图谱构建跳过：文档未解析完成 doc_id=%s, status=%s", doc_id, doc.status)
                return
            file_name = doc.file_name
            await repo.update_graph_status(doc_id, "building")

        # 清旧贡献（幂等：无论是否构建过，先清再建）
        await _clear_file_contributions(kb_id, doc_id)

        # 取该文件切片并过滤短 chunk
        async with async_session() as session:
            repo = DocumentRepository(session)
            chunks = await repo.list_chunks_for_graph(doc_id)
        min_chars = settings.graph_min_chunk_chars
        valid = [
            {"id": str(c.id), "text": c.text_content, "page": c.page_number}
            for c in chunks if len(c.text_content or "") >= min_chars
        ]
        logger.info("图谱构建: doc_id=%s, chunks=%d (过滤后 %d)", doc_id, len(chunks), len(valid))
        if not valid:
            await _set_graph_completed(doc_id, started, 0, 0)
            return

        # 抽取（词库 + 正则 + LLM）
        result = await extract_from_chunks(kb_id, valid)

        # 写图（按 chunk 批次拆分事务，§4.4）
        statements = _build_write_statements(
            kb_id, doc_id, file_name, result["entities"], result["relationships"]
        )
        await _write_in_batches(statements)

        await _set_graph_completed(doc_id, started, len(result["entities"]), len(result["relationships"]))
    except GraphDisabledError:
        raise
    except Exception as e:
        error_msg = str(e)[:1000]
        logger.error("图谱构建失败: doc_id=%s, error=%s", doc_id, error_msg, exc_info=True)
        try:
            async with async_session() as session:
                repo = DocumentRepository(session)
                await repo.update_graph_status(doc_id, "failed", error_message=error_msg)
        except Exception as db_err:
            logger.error("图谱失败状态落库异常: %s", db_err)


async def _set_graph_completed(doc_id: str, started: float, entities: int, relations: int) -> None:
    duration_ms = int((time.perf_counter() - started) * 1000)
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.update_graph_status(doc_id, "completed", duration_ms=duration_ms)
    logger.info("图谱构建完成: doc_id=%s, entities=%d, relations=%d, %dms",
                doc_id, entities, relations, duration_ms)


async def build_kb_graph(kb_id: str) -> None:
    """全量构建 — 清空该知识库图谱 → 逐文件构建（任一失败不中断）。"""
    _ensure_graph_enabled()
    try:
        # 1. 取该知识库全部已解析文档
        async with async_session() as session:
            repo = DocumentRepository(session)
            docs = await repo.list_by_kb(kb_id)
        completed = [d for d in docs if d.status == "completed"]

        # 2. 清空整个知识库图谱（Neo4j + PG 状态）
        await Neo4jStore.execute_write(
            "MATCH (n) WHERE n.kb_id = $kb_id DETACH DELETE n", {"kb_id": kb_id}
        )
        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.reset_graph_status_by_kb(kb_id)

        # 3. 逐文件构建
        for doc in completed:
            try:
                await build_file_graph(kb_id, str(doc.id))
            except Exception as e:
                logger.error("全量构建单个文件失败: doc_id=%s, error=%s", doc.id, e)
        logger.info("图谱全量构建完成: kb_id=%s, docs=%d", kb_id, len(completed))
    except GraphDisabledError:
        raise
    except Exception as e:
        logger.error("图谱全量构建异常: kb_id=%s, error=%s", kb_id, e, exc_info=True)
        raise


async def clear_file_graph(kb_id: str, doc_id: str) -> None:
    """清除单文件图谱（G-05，手动清除；失败抛异常由 router 处理）。"""
    _ensure_graph_enabled()
    try:
        await _clear_file_contributions(kb_id, doc_id)
        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.update_graph_status(doc_id, "none")
        logger.info("单文件图谱已清除: kb_id=%s, doc_id=%s", kb_id, doc_id)
    except GraphDisabledError:
        raise
    except Exception as e:
        logger.error("清除单文件图谱失败: doc_id=%s, error=%s", doc_id, e, exc_info=True)
        raise


async def clear_file_graph_best_effort(kb_id: str, doc_id: str) -> None:
    """文档删除/重切片联动清除（best-effort，§4.3 调用契约）。

    顺序：先取 chunk_ids（在删 PG 行/切片之前）→ 清 Neo4j → 状态置 none。
    失败不阻断主流程：记日志并将该文档 graph_status 置 failed（重建幂等兜底）。
    """
    try:
        await _clear_file_contributions(kb_id, doc_id)
        async with async_session() as session:
            repo = DocumentRepository(session)
            await repo.update_graph_status(doc_id, "none")
        logger.info("联动清除图谱完成: kb_id=%s, doc_id=%s", kb_id, doc_id)
    except GraphDisabledError:
        pass  # 图谱未启用：无需清除
    except Exception as e:
        logger.error("联动清除图谱失败（不阻断主流程）: doc_id=%s, error=%s", doc_id, e)
        try:
            async with async_session() as session:
                repo = DocumentRepository(session)
                await repo.update_graph_status(doc_id, "failed", error_message=str(e)[:1000])
        except Exception as db_err:
            logger.error("图谱失败状态落库异常: %s", db_err)


async def clear_kb_graph(kb_id: str) -> None:
    """清空整个知识库图谱。"""
    _ensure_graph_enabled()
    await Neo4jStore.execute_write(
        "MATCH (n) WHERE n.kb_id = $kb_id DETACH DELETE n", {"kb_id": kb_id}
    )
    async with async_session() as session:
        repo = DocumentRepository(session)
        await repo.reset_graph_status_by_kb(kb_id)
    logger.info("知识库图谱已清空: kb_id=%s", kb_id)


# ============================================================
# 图谱查询（stats / 全图 / 检索）
# ============================================================

async def get_graph_stats(kb_id: str) -> dict:
    """图谱概要统计：Neo4j 实体/关系/类型分布 + PG 构建进度。"""
    _ensure_graph_enabled()

    async def _count(cypher: str, params: dict) -> int:
        rows = await Neo4jStore.execute_read(cypher, params)
        return rows[0]["cnt"] if rows else 0

    total_entities = await _count(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id RETURN count(n) AS cnt", {"kb_id": kb_id}
    )
    total_relations = await _count(
        "MATCH ()-[r]->() WHERE r.kb_id = $kb_id AND type(r) <> '提及' RETURN count(r) AS cnt",
        {"kb_id": kb_id},
    )
    entity_types = {
        row["type"]: row["cnt"]
        for row in await Neo4jStore.execute_read(
            "MATCH (n:Entity) WHERE n.kb_id = $kb_id RETURN n.type AS type, count(*) AS cnt",
            {"kb_id": kb_id},
        )
    }
    relation_types = {
        row["type"]: row["cnt"]
        for row in await Neo4jStore.execute_read(
            "MATCH ()-[r]->() WHERE r.kb_id = $kb_id AND type(r) <> '提及' "
            "RETURN type(r) AS type, count(*) AS cnt",
            {"kb_id": kb_id},
        )
    }

    # PG 构建进度
    async with async_session() as session:
        repo = DocumentRepository(session)
        docs = await repo.list_by_kb(kb_id)
    build = {"doc_total": 0, "built": 0, "building": 0, "failed": 0, "none": 0}
    for d in docs:
        build["doc_total"] += 1
        if d.graph_status == "completed":
            build["built"] += 1
        elif d.graph_status == "building":
            build["building"] += 1
        elif d.graph_status == "failed":
            build["failed"] += 1
        else:
            build["none"] += 1

    return {
        "kb_id": kb_id,
        "graph_exists": total_entities > 0,
        "stats": {
            "total_entities": total_entities,
            "total_relations": total_relations,
            "entity_types": entity_types,
            "relation_types": relation_types,
        },
        "build": build,
    }


async def get_graph_data(
    kb_id: str,
    limit: int = 150,
    offset: int = 0,
    exclude_names: list[str] | None = None,
) -> dict:
    """图谱数据（ECharts 渲染，分页分层加载避免卡顿）。

    节点按 degree 降序分页；增量加载时传 exclude_names（已加载节点名），
    返回新批节点 + 「已加载 ∪ 新节点」之间的边（跨批边不断裂），
    附 total_nodes / has_more 供前端"加载更多"。
    """
    _ensure_graph_enabled()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    exclude = exclude_names or []

    total_rows = await Neo4jStore.execute_read(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id RETURN count(n) AS c",
        {"kb_id": kb_id},
    )
    total_nodes = total_rows[0]["c"] if total_rows else 0

    if exclude:
        nodes = await Neo4jStore.execute_read(
            "MATCH (n:Entity) WHERE n.kb_id = $kb_id AND NOT n.name IN $exclude "
            "RETURN n.name AS name, n.type AS type, COUNT { (n)--() } AS degree "
            "ORDER BY degree DESC SKIP $offset LIMIT $limit",
            {"kb_id": kb_id, "exclude": exclude, "offset": offset, "limit": limit},
        )
    else:
        nodes = await Neo4jStore.execute_read(
            "MATCH (n:Entity) WHERE n.kb_id = $kb_id "
            "RETURN n.name AS name, n.type AS type, COUNT { (n)--() } AS degree "
            "ORDER BY degree DESC SKIP $offset LIMIT $limit",
            {"kb_id": kb_id, "offset": offset, "limit": limit},
        )

    node_names = [n["name"] for n in nodes]
    edge_names = list(dict.fromkeys(exclude + node_names))  # 已加载 ∪ 新节点
    edges = []
    if edge_names:
        edges = await Neo4jStore.execute_read(
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.kb_id = $kb_id AND b.kb_id = $kb_id AND type(r) <> '提及' "
            "AND a.name IN $names AND b.name IN $names "
            "RETURN a.name AS source, a.type AS source_type, type(r) AS type, "
            "b.name AS target, b.type AS target_type LIMIT 5000",
            {"kb_id": kb_id, "names": edge_names},
        )
    return {
        "nodes": [
            {
                "id": f"{n['type']}_{n['name']}",
                "name": n["name"],
                "type": n["type"],
                "degree": n["degree"],
            }
            for n in nodes
        ],
        "edges": [
            {
                "source": f"{e['source_type']}_{e['source']}",
                "target": f"{e['target_type']}_{e['target']}",
                "type": e["type"],
            }
            for e in edges
        ],
        "total_nodes": total_nodes,
        "has_more": offset + len(nodes) < total_nodes,
    }


async def _resolve_evidence(kb_id: str, chunk_ids: list[str]) -> list[dict]:
    """按 chunk_id 列表查 document_chunks 取证据明细（含文件名）。"""
    if not chunk_ids:
        return []
    async with async_session() as session:
        repo = DocumentRepository(session)
        chunks = await repo.get_chunks_by_ids(kb_id, chunk_ids)
        doc_ids = list({c.doc_id for c in chunks})
        docs = await repo.get_by_ids(doc_ids)
    file_map = {str(d.id): d.file_name for d in docs}
    rows = []
    for c in chunks:
        rows.append({
            "doc_id": str(c.doc_id),
            "file_name": file_map.get(str(c.doc_id), ""),
            "chunk_id": str(c.id),
            "chunk_index": c.chunk_index,
            "page_number": c.page_number,
            "text": c.text_content or "",
        })
    rows.sort(key=lambda x: (x["page_number"], x["chunk_index"]))
    return rows


async def search_subgraph(kb_id: str, query: str, max_hops: int = 2, limit: int = 200) -> dict:
    """子图检索：词库匹配 query → Neo4j 入口实体 + 邻居展开 → chunk 证据。"""
    _ensure_graph_enabled()
    max_hops = max(1, min(max_hops, 3))
    limit = max(1, min(limit, 1000))

    # 1. 实体定位：词库匹配（含别名→标准词）+ 正则层（金额/日期，§6.1）
    await lexicon_service.ensure_cache()
    candidates = [e["name"] for e in lexicon_service.match_entities(query)]
    for ent in _regex_match_entities(query):
        if ent["name"] not in candidates:
            candidates.append(ent["name"])
    if not candidates:
        candidates = [query.strip()]

    # 2. Neo4j 子图（入口 + 邻居，过滤 提及 关系）
    # 跳数不能参数化（该实例拒绝参数化变量长度关系），max_hops 已 clamp 1..3，拼接安全
    # 入口匹配：词库/正则候选精确匹配 + name 包含匹配兜底（§6.1 精确/包含匹配）
    nodes = await Neo4jStore.execute_read(
        "MATCH (start:Entity) WHERE start.kb_id = $kb_id "
        "AND (start.name IN $candidates OR start.name CONTAINS $q) "
        f"MATCH (start)-[r*0..{max_hops}]-(n:Entity) WHERE n.kb_id = $kb_id "
        "AND all(x IN r WHERE x.kb_id = $kb_id AND type(x) <> '提及') "
        "WITH collect(DISTINCT n) AS node_list LIMIT $limit "
        "UNWIND node_list AS node "
        "RETURN DISTINCT node.name AS name, node.type AS type, COUNT { (node)--() } AS degree",
        {"kb_id": kb_id, "candidates": candidates, "q": query.strip(), "limit": limit},
    )
    if not nodes:
        return {"nodes": [], "edges": [], "evidence": []}

    node_names = [n["name"] for n in nodes]
    edges = await Neo4jStore.execute_read(
        "MATCH (a:Entity)-[r]->(b:Entity) "
        "WHERE a.kb_id = $kb_id AND b.kb_id = $kb_id AND type(r) <> '提及' "
        "AND a.name IN $names AND b.name IN $names "
        "RETURN a.name AS source, a.type AS source_type, type(r) AS type, "
        "b.name AS target, b.type AS target_type",
        {"kb_id": kb_id, "names": node_names},
    )

    # 3. chunk 证据：收集全部节点的 mentioned_chunk_ids → PG 查询
    node_rows = await Neo4jStore.execute_read(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id AND n.name IN $names "
        "RETURN n.name AS name, n.mentioned_chunk_ids AS chunk_ids",
        {"kb_id": kb_id, "names": node_names},
    )
    chunk_ids: list[str] = []
    for row in node_rows:
        for cid in row.get("chunk_ids") or []:
            if cid not in chunk_ids:
                chunk_ids.append(cid)
    evidence_rows = await _resolve_evidence(kb_id, chunk_ids)
    evidence_map: dict[str, list[dict]] = {}
    for row in node_rows:
        name = row["name"]
        ids = row.get("chunk_ids") or []
        evidence_map[name] = [e for e in evidence_rows if e["chunk_id"] in ids]

    return {
        "nodes": [
            {"id": f"{n['type']}_{n['name']}", "name": n["name"], "type": n["type"], "degree": n["degree"]}
            for n in nodes
        ],
        "edges": [
            {
                "source": f"{e['source_type']}_{e['source']}",
                "target": f"{e['target_type']}_{e['target']}",
                "type": e["type"],
            }
            for e in edges
        ],
        "evidence": [
            {"entity": name, "chunks": chunks} for name, chunks in evidence_map.items() if chunks
        ],
    }


async def expand_node(kb_id: str, name: str, limit: int = 100) -> dict | None:
    """展开指定实体的 1-hop 邻居（分级浏览核心原语）。

    返回邻居节点 + 「该实体 ∪ 邻居」之间的边；实体不存在返回 None。
    节点数每次只增加 1-hop 范围，前端合入画布去重，避免全量渲染卡顿。
    """
    _ensure_graph_enabled()
    limit = max(1, min(limit, 300))

    exists = await Neo4jStore.execute_read(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id AND n.name = $name RETURN count(n) AS c",
        {"kb_id": kb_id, "name": name},
    )
    if not exists or exists[0]["c"] == 0:
        return None

    neighbors = await Neo4jStore.execute_read(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id AND n.name = $name "
        "MATCH (n)-[r]-(m:Entity) WHERE m.kb_id = $kb_id AND r.kb_id = $kb_id "
        "AND type(r) <> '提及' "
        "WITH DISTINCT m, COUNT { (m)--() } AS degree LIMIT $limit "
        "RETURN m.name AS name, m.type AS type, degree",
        {"kb_id": kb_id, "name": name, "limit": limit},
    )

    edge_names = list(dict.fromkeys([name] + [nb["name"] for nb in neighbors]))
    edges = []
    if len(edge_names) >= 2:
        edges = await Neo4jStore.execute_read(
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.kb_id = $kb_id AND b.kb_id = $kb_id AND type(r) <> '提及' "
            "AND a.name IN $names AND b.name IN $names "
            "RETURN a.name AS source, a.type AS source_type, type(r) AS type, "
            "b.name AS target, b.type AS target_type LIMIT 1000",
            {"kb_id": kb_id, "names": edge_names},
        )

    return {
        "expanded": name,
        "nodes": [
            {"id": f"{nb['type']}_{nb['name']}", "name": nb["name"], "type": nb["type"], "degree": nb["degree"]}
            for nb in neighbors
        ],
        "edges": [
            {
                "source": f"{e['source_type']}_{e['source']}",
                "target": f"{e['target_type']}_{e['target']}",
                "type": e["type"],
            }
            for e in edges
        ],
    }


async def get_entity_detail(kb_id: str, name: str) -> dict | None:
    """实体详情：属性 + 1-hop 邻居 + chunk 证据。"""
    _ensure_graph_enabled()
    rows = await Neo4jStore.execute_read(
        "MATCH (n:Entity) WHERE n.kb_id = $kb_id AND n.name = $name "
        "RETURN n.name AS name, n.type AS type, n.description AS description, "
        "COUNT { (n)--() } AS degree, n.mentioned_chunk_ids AS chunk_ids",
        {"kb_id": kb_id, "name": name},
    )
    if not rows:
        return None
    row = rows[0]

    neighbors = await Neo4jStore.execute_read(
        "MATCH (n:Entity)-[r]-(m:Entity) "
        "WHERE n.kb_id = $kb_id AND n.name = $name AND m.kb_id = $kb_id "
        "AND r.kb_id = $kb_id AND type(r) <> '提及' "
        "RETURN m.name AS name, m.type AS type, type(r) AS relation, "
        "CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction",
        {"kb_id": kb_id, "name": name},
    )

    evidence = await _resolve_evidence(kb_id, row.get("chunk_ids") or [])

    return {
        "entity": {
            "name": row["name"],
            "type": row["type"],
            "description": row.get("description") or "",
            "degree": row["degree"],
        },
        "neighbors": [
            {
                "name": nb["name"],
                "type": nb["type"],
                "relation": nb["relation"],
                "direction": nb["direction"],
            }
            for nb in neighbors
        ],
        "evidence": evidence,
    }


# ============================================================
# 队列入口（供 graph_queue worker 调用）
# ============================================================

async def build_file_task(kb_id: str, doc_id: str) -> int:
    """入队单文件图谱构建。"""
    return await graph_queue.enqueue(kb_id, doc_id)


async def build_kb_task(kb_id: str) -> int:
    """入队全量图谱构建。"""
    return await graph_queue.enqueue_build_kb(kb_id)


async def clear_file_task(kb_id: str, doc_id: str) -> int:
    """入队清除单文件图谱。"""
    return await graph_queue.enqueue_clear_file(kb_id, doc_id)
