"""
Elasticsearch 全文检索封装 — 索引管理、双写同步、检索（IK 分词 + 高亮）。

设计约束：
- ES 是派生数据源（PG document_chunks 为权威源），所有写操作 best-effort：
  失败仅记日志返回 False，由对账/重建（routers/es_admin.py）兜底；
- 所有方法在 ES 不可用时优雅降级，不阻断解析/检索主流程；
- 单例 AsyncElasticsearch，应用关闭时 close（见 main.py lifespan）。
"""
import time
from typing import Any

from elasticsearch import AsyncElasticsearch, NotFoundError
from langfuse import get_client as lf_get_client, observe

from config import settings
from core.observability import format_retrieval_observation
from utils.logging import get_logger

logger = get_logger(__name__)

_client: AsyncElasticsearch | None = None

# 分析器模式："ik"（IK 插件，自定义词典）| "cjk"（内置 bigram，无插件降级）
# 首次 ensure_index 探测：IK 插件缺失时自动降级并记住结果
_analyzer_mode: str | None = None

# 参与 IK 分词的文本字段（multi_match 检索时加权）
_TEXT_FIELDS = ["text", "heading", "heading_path", "file_name_search"]


def get_client() -> AsyncElasticsearch:
    """获取（惰性创建）ES 异步客户端单例。"""
    global _client
    if _client is None:
        kwargs: dict[str, Any] = dict(
            hosts=[f"http://{settings.es_host}:{settings.es_port}"],
            request_timeout=10,
        )
        if settings.es_username:
            kwargs["basic_auth"] = (settings.es_username, settings.es_password)
        _client = AsyncElasticsearch(**kwargs)
    return _client


def _index_body(mode: str) -> dict:
    """按分析器模式构建索引 settings/mappings。"""
    if mode == "ik":
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.refresh_interval": "1s",
            "analysis": {
                "analyzer": {
                    "ik_search": {"type": "custom", "tokenizer": "ik_smart"},
                    "ik_index": {"type": "custom", "tokenizer": "ik_max_word"},
                }
            },
        }
        index_analyzer, search_analyzer = "ik_index", "ik_search"
    else:
        # 内置 cjk 分析器（bigram，无需插件），中文检索可用性兜底
        settings = {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "index.refresh_interval": "1s",
        }
        index_analyzer = search_analyzer = "cjk"

    def text_field() -> dict:
        return {"type": "text", "analyzer": index_analyzer, "search_analyzer": search_analyzer}

    return {
        "settings": settings,
        "mappings": {
            "properties": {
                "doc_id": {"type": "keyword"},
                "kb_id": {"type": "keyword"},
                "file_name": {"type": "keyword"},
                "file_name_search": text_field(),
                "page_number": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "chunk_type": {"type": "keyword"},
                "source_chunk_index": {"type": "integer"},
                "heading": text_field(),
                "heading_path": text_field(),
                "text": text_field(),
            }
        },
    }


async def ensure_index() -> bool:
    """幂等创建 bid_chunks 索引（IK 分词优先，缺失时降级内置 cjk）。

    IK 插件未安装时索引创建会报 "unknown analyzer" 错误，自动降级为
    cjk 分析器（中文 bigram 检索），保证全文检索可用。
    """
    global _analyzer_mode
    client = get_client()
    try:
        if await client.indices.exists(index=settings.es_index_name):
            return True
        try:
            await client.indices.create(
                index=settings.es_index_name,
                **_index_body("ik"),
            )
            _analyzer_mode = "ik"
            logger.info("ES 索引已创建: %s（IK 分析器）", settings.es_index_name)
            return True
        except Exception as e:
            if _analyzer_mode is not None:
                raise  # 已降级过仍失败，交给上层
            if "analyzer" not in str(e).lower():
                raise
            logger.warning(
                "IK 插件不可用，ES 索引降级为内置 cjk 分析器（中文按 bigram 检索）: %s", e
            )
            _analyzer_mode = "cjk"
            await client.indices.create(
                index=settings.es_index_name,
                **_index_body("cjk"),
            )
            logger.info("ES 索引已创建: %s（cjk 分析器）", settings.es_index_name)
            return True
    except Exception as e:
        logger.error("创建 ES 索引失败: %s", e, exc_info=True)
        return False


async def close_client() -> None:
    """应用关闭时释放客户端（幂等）。"""
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception as e:
            logger.warning("关闭 ES 客户端失败: %s", e)
        _client = None


def _doc_id(doc_id: str, chunk_index: int) -> str:
    """ES 文档 ID：doc_id + chunk_index 幂等覆盖。"""
    return f"{doc_id}_{chunk_index}"


async def bulk_index(
    chunks: list[str],
    metadata_list: list[dict[str, Any]],
    kb_id: str,
) -> bool:
    """批量写入切片到 ES（解析批次内调用，与 Milvus/PG 并行落库）。

    Args:
        chunks: 文本切片列表
        metadata_list: 与 chunks 一一对应的元数据（doc_id, file_name, page_number, chunk_index...）
        kb_id: 知识库 ID

    Returns:
        是否全部写入成功（False 表示失败，由对账兜底）
    """
    if not settings.es_sync_enabled:
        return True
    if not chunks:
        return True

    client = get_client()
    operations: list[dict] = []
    for i, (text, meta) in enumerate(zip(chunks, metadata_list)):
        chunk_index = meta.get("chunk_index", i)
        operations.append(
            {
                "index": {
                    "_index": settings.es_index_name,
                    "_id": _doc_id(meta.get("doc_id", ""), chunk_index),
                }
            }
        )
        operations.append(
            {
                "doc_id": meta.get("doc_id", ""),
                "kb_id": kb_id,
                "file_name": meta.get("file_name", ""),
                "page_number": meta.get("page_number", 1),
                "chunk_index": chunk_index,
                "chunk_type": meta.get("chunk_type", "text"),
                "source_chunk_index": meta.get("source_chunk_index", -1),
                "heading": meta.get("heading", ""),
                "heading_path": meta.get("heading_path", ""),
                "text": text,
            }
        )

    try:
        resp = await client.bulk(operations=operations)
        if resp.get("errors"):
            failed = [
                (item.get("index", {}).get("_id"))
                for item in resp.get("items", [])
                if item.get("index", {}).get("status", 200) >= 300
            ]
            logger.error("ES bulk 写入部分失败: %d 条（%s...）", len(failed), failed[:5])
            return False
        logger.debug("ES bulk 写入完成: kb_id=%s, docs=%d", kb_id, len(chunks))
        return True
    except Exception as e:
        logger.error("ES bulk 写入异常: kb_id=%s, error=%s", kb_id, e)
        return False


async def delete_by_doc(doc_id: str) -> bool:
    """删除某文档的全部 ES 文档（按 doc_id term）。"""
    return await _delete_by_query({"term": {"doc_id": doc_id}})


async def delete_by_kb(kb_id: str) -> bool:
    """删除某知识库的全部 ES 文档（按 kb_id term）。"""
    return await _delete_by_query({"term": {"kb_id": kb_id}})


async def delete_by_chunk(doc_id: str, chunk_index: int) -> bool:
    """删除单条切片（按 _id 精确删除；不存在视为成功）。"""
    if not settings.es_sync_enabled:
        return True
    client = get_client()
    try:
        await client.delete(index=settings.es_index_name, id=_doc_id(doc_id, chunk_index))
        return True
    except NotFoundError:
        return True
    except Exception as e:
        logger.warning("ES 删除切片失败: doc_id=%s chunk=%d, error=%s", doc_id, chunk_index, e)
        return False


async def _delete_by_query(query: dict) -> bool:
    if not settings.es_sync_enabled:
        return True
    client = get_client()
    try:
        resp = await client.delete_by_query(index=settings.es_index_name, query=query)
        return True
    except Exception as e:
        logger.warning("ES delete_by_query 失败: %s, error=%s", query, e)
        return False


@observe(as_type="retriever", capture_input=False, capture_output=False)
async def es_search(
    query: str | list[str],
    kb_ids: list[str],
    top_k: int = 10,
    highlight: bool = True,
) -> list[dict[str, Any]]:
    """ES 全文检索（IK multi_match + kb 过滤 + 高亮）。

    query 支持字符串或查询词列表（词库查询扩展后的 OR 语义，见 lexicon_service.expand_query）。

    Returns:
        与 retrieve() 相同结构的命中列表，额外携带 highlight/kb_id 字段:
        [{score, text, doc_id, file_name, page_number, chunk_index,
          chunk_type, source_chunk_index, kb_id, highlight}, ...]
    """
    t0 = time.perf_counter()
    queries = [query] if isinstance(query, str) else list(query)
    queries = [q for q in queries if q and q.strip()]
    if not kb_ids or not queries:
        lf_get_client().update_current_span(
            input=query,
            metadata={
                "method": "fulltext",
                "kb_ids": kb_ids,
                "top_k": top_k,
                "hit_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
            output=format_retrieval_observation([]),
        )
        return []

    # 字段加权：text 主字段权重最高
    field_weights = {"text": 3, "heading": 2}
    search_fields = [
        f"{f}^{field_weights.get(f, 1)}"
        for f in _TEXT_FIELDS
        if f in ("text", "heading", "heading_path", "file_name_search")
    ]

    search_kwargs: dict[str, Any] = {
        "index": settings.es_index_name,
        "size": top_k,
        "query": {
            "bool": {
                "should": [
                    {
                        "multi_match": {
                            "query": q,
                            "fields": search_fields,
                            "type": "best_fields",
                        }
                    }
                    for q in queries
                ],
                "minimum_should_match": 1,
                "filter": [{"terms": {"kb_id": kb_ids}}],
            }
        },
    }
    if highlight and settings.es_highlight_enabled:
        search_kwargs["highlight"] = {
            "fields": {
                "text": {
                    "fragment_size": 200,
                    "number_of_fragments": 1,
                    "pre_tags": ["<em>"],
                    "post_tags": ["</em>"],
                }
            }
        }

    client = get_client()
    try:
        resp = await client.search(**search_kwargs)
    except Exception as e:
        logger.warning("ES 检索失败: query=%s, error=%s", queries[0][:50], e)
        lf_get_client().update_current_span(
            input=query,
            metadata={
                "method": "fulltext",
                "kb_ids": kb_ids,
                "top_k": top_k,
                "hit_count": 0,
                "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
            output=format_retrieval_observation([]),
        )
        return []

    hits: list[dict[str, Any]] = []
    for h in resp.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        hl = h.get("highlight") or {}
        hl_parts = hl.get("text") or []
        hits.append(
            {
                "score": round(h.get("_score") or 0.0, 4),
                "text": src.get("text", ""),
                "doc_id": src.get("doc_id", ""),
                "file_name": src.get("file_name", ""),
                "page_number": src.get("page_number", 1),
                "chunk_index": src.get("chunk_index", 0),
                "chunk_type": src.get("chunk_type", "text"),
                "source_chunk_index": src.get("source_chunk_index", -1),
                "kb_id": src.get("kb_id", ""),
                "highlight": "".join(hl_parts) if hl_parts else None,
            }
        )
    lf_get_client().update_current_span(
        input=query,
        metadata={
            "method": "fulltext",
            "kb_ids": kb_ids,
            "top_k": top_k,
            "hit_count": len(hits),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
        output=format_retrieval_observation(hits),
    )
    return hits


async def kb_stats() -> dict[str, dict]:
    """按知识库统计 ES 数据：{kb_id: {"chunks": 切片数, "docs": 文档数}}（对账用）。"""
    client = get_client()
    try:
        resp = await client.search(
            index=settings.es_index_name,
            size=0,
            aggs={
                "by_kb": {
                    "terms": {"field": "kb_id", "size": 1000},
                    "aggs": {"docs": {"cardinality": {"field": "doc_id"}}},
                }
            },
        )
        buckets = resp.get("aggregations", {}).get("by_kb", {}).get("buckets", [])
        return {
            b["key"]: {"chunks": b["doc_count"], "docs": b["docs"]["value"]}
            for b in buckets
        }
    except Exception as e:
        logger.warning("ES 按库统计失败（可能未启动）: %s", e)
        return {}


async def count_by_doc() -> dict[str, int]:
    """统计每个文档的 ES 切片数：{doc_id: count}（文档级对账用）。"""
    client = get_client()
    try:
        resp = await client.search(
            index=settings.es_index_name,
            size=0,
            aggs={"by_doc": {"terms": {"field": "doc_id", "size": 10000}}},
        )
        buckets = resp.get("aggregations", {}).get("by_doc", {}).get("buckets", [])
        return {b["key"]: b["doc_count"] for b in buckets}
    except Exception as e:
        logger.warning("ES 按文档统计失败（可能未启动）: %s", e)
        return {}


async def index_stats() -> dict | None:
    """ES 索引健康/存储统计（对账页展示用）。ES 不可用时返回 None。"""
    client = get_client()
    try:
        resp = await client.cat.indices(index=settings.es_index_name, format="json")
        if not resp:
            return None
        r = resp[0]
        return {
            "health": r.get("health", "unknown"),
            "docs_count": int(r.get("docs.count", 0) or 0),
            "deleted_docs": int(r.get("docs.deleted", 0) or 0),
            "store_size": r.get("store.size", ""),
            "segments": int(r.get("segments.count", 0) or 0),
        }
    except Exception as e:
        logger.warning("ES 索引统计失败（可能未启动）: %s", e)
        return None


async def ping() -> bool:
    """ES 健康检查（/api/es/status 用）。"""
    client = get_client()
    try:
        return bool(await client.ping())
    except Exception:
        return False
