"""
检索引擎 — llama_index VectorStoreIndex + Retriever + SimilarityPostprocessor。
"""
import time
from collections.abc import Callable
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.schema import QueryBundle
from llama_index.core.vector_stores.types import VectorStoreQueryMode
from langfuse import get_client

from config import settings
from core import es_store
from core.model_factory import create_embedding, create_reranker, resolve_model_config
from core.observability import format_retrieval_observation, traced_span
from core.query_rewrite import rewrite_query
from core.vector_store import get_vector_store
from utils.logging import get_logger

logger = get_logger(__name__)

RETRIEVAL_METHODS = ("dense", "sparse", "fulltext")


def rrf_merge(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """RRF 融合多路召回（按 (doc_id, chunk_index) 对齐 rank 分数）。

    与 search 页共用的融合实现；融合分数非 [0,1]，仅作排序依据。
    """
    scores: dict[tuple, float] = {}
    hits_by_key: dict[tuple, dict] = {}
    for ranked in ranked_lists:
        for i, h in enumerate(ranked):
            key = (h.get("doc_id", ""), h.get("chunk_index", -1))
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
            hits_by_key[key] = h
    merged = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        h = dict(hits_by_key[key])
        h["score"] = round(score, 4)
        h.setdefault("highlight", None)
        h.setdefault("kb_id", "")
        merged.append(h)
    return merged


def _resolve_query_mode(retrieval_method: str | None) -> VectorStoreQueryMode:
    """把检索策略解析为 Milvus 查询模式：dense=稠密向量，sparse=BM25 稀疏向量。"""
    if retrieval_method in (None, "dense"):
        return VectorStoreQueryMode.DEFAULT
    if retrieval_method == "sparse":
        return VectorStoreQueryMode.SPARSE
    raise ValueError(f"不支持的检索方式: {retrieval_method}")


@traced_span("embedding", "embedding")
async def _embed_query(
    query: str,
    embed_model: Any,
    dim: int,
    embed_config: dict,
) -> list[float]:
    """Query 向量化（embedding 子观测，SDK embedding 类型）：调用外部 embedding API。"""
    query_embedding = await embed_model.aget_query_embedding(query)
    get_client().update_current_span(
        input=query,
        metadata={
            "model": embed_config.get("model"),
            "dim": dim,
            "api_base": embed_config.get("api_base"),
        },
        output={"embedding_dim": len(query_embedding)},
    )
    return query_embedding


@traced_span("milvus_query")
async def _milvus_search(
    query: str,
    retriever: Any,
    query_bundle: QueryBundle,
    collection: str,
    mode: str,
    top_k: int,
    similarity_threshold: float,
):
    """Milvus ANN 检索（子 span，SPAN）：执行检索并记录集合/模式/命中数。"""
    nodes = await retriever.aretrieve(query_bundle)
    get_client().update_current_span(
        input=query,
        metadata={
            "collection": collection,
            "mode": mode,
            "top_k": top_k,
            "similarity_threshold": similarity_threshold,
        },
        output={"hit_count": len(nodes)},
    )
    return nodes


@traced_span("retrieve")
async def retrieve(
    query: str,
    kb_id: str,
    top_k: int = 5,
    similarity_threshold: float = 0.6,
    retrieval_method: str | None = None,
) -> list[dict[str, Any]]:
    """在指定知识库中执行单路检索。

    Args:
        query: 检索关键词或自然语言描述
        kb_id: 目标知识库 ID
        top_k: 返回的最相关结果数
        similarity_threshold: 相似度阈值（IP 分数），低于此分数丢弃；仅 dense 生效
        retrieval_method: 检索策略：dense（稠密向量）/ sparse（BM25 稀疏向量），为空默认 dense

    Returns:
        按分数降序排列的结果列表:
        [{score, text, doc_id, file_name, page_number, chunk_index}, ...]
    """
    mode = _resolve_query_mode(retrieval_method)
    is_sparse = mode == VectorStoreQueryMode.SPARSE
    retrieval_metadata = {
        "method": "sparse" if is_sparse else "dense",
        "kb_id": kb_id,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
    }

    # 1. resolve Embedding 配置（维度 + client）
    embed_config = await resolve_model_config(kb_id, "embedding")
    dim = (embed_config.get("params") or {}).get("dimension") or settings.embedding_dim
    embed_model = create_embedding(embed_config)

    # 2. 创建 vector_store + index
    vector_store = get_vector_store(kb_id, dim)

    # 检查 Collection 是否存在
    if vector_store.collection_name not in vector_store.client.list_collections():
        logger.warning("retrieve: Collection 不存在 %s", vector_store.collection_name)
        get_client().update_current_span(
            input=query,
            metadata=retrieval_metadata,
            output=format_retrieval_observation([]),
        )
        return []

    # 3. 构建 VectorStoreIndex + Retriever
    index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)
    retriever = index.as_retriever(
        similarity_top_k=top_k, vector_store_query_mode=mode
    )

    # 4. 查询向量化（sparse 用 BM25 内置函数，无需 embedding）
    if is_sparse:
        query_bundle = QueryBundle(query_str=query)
        embed_ms = None
    else:
        t_embed = time.perf_counter()
        query_embedding = await _embed_query(query, embed_model, dim, embed_config)
        embed_ms = round((time.perf_counter() - t_embed) * 1000, 2)
        query_bundle = QueryBundle(query_str=query, embedding=query_embedding)

    # 5. 检索（Milvus 查询独立子 span）
    t_search = time.perf_counter()
    nodes = await _milvus_search(
        query=query,
        retriever=retriever,
        query_bundle=query_bundle,
        collection=vector_store.collection_name,
        mode="sparse" if is_sparse else "dense",
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )
    search_ms = round((time.perf_counter() - t_search) * 1000, 2)

    # 6. 后处理：相似度阈值过滤（BM25 稀疏分数非 [0,1]，仅稠密模式适用）
    t_filter = time.perf_counter()
    if is_sparse:
        filtered_nodes = nodes
    else:
        processor = SimilarityPostprocessor(similarity_cutoff=similarity_threshold)
        filtered_nodes = processor.postprocess_nodes(nodes)
    filter_ms = round((time.perf_counter() - t_filter) * 1000, 2)

    # 7. 组装为 service 层兼容的 dict 格式
    hits = []
    for node_with_score in filtered_nodes:
        node = node_with_score.node
        hits.append({
            "score": round(node_with_score.score, 4) if node_with_score.score else 0.0,
            "text": node.text,
            "doc_id": node.metadata.get("doc_id", ""),
            "file_name": node.metadata.get("file_name", ""),
            "page_number": node.metadata.get("page_number", 1),
            "chunk_index": node.metadata.get("chunk_index", 0),
            "chunk_type": node.metadata.get("chunk_type", "text"),
            "source_chunk_index": node.metadata.get("source_chunk_index", -1),
        })

    retrieval_metadata.update({
        "embed_ms": embed_ms,
        "search_ms": search_ms,
        "filter_ms": filter_ms,
        "embedding_model": embed_config.get("model"),
        "dim": dim,
        "collection": vector_store.collection_name,
    })
    get_client().update_current_span(
        input=query,
        metadata=retrieval_metadata,
        output=format_retrieval_observation(hits),
    )

    logger.info(
        "retrieve 完成: kb_id=%s, query=%s, top_k=%d, found=%d",
        kb_id,
        query[:80],
        top_k,
        len(hits),
    )
    return hits


@traced_span("rerank", "generation")
async def rerank(
    query: str,
    hits: list[dict[str, Any]],
    config: dict,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """对候选命中做重排，用 rerank relevance_score 覆盖 score 并按降序返回。

    检索链路的一步（召回 → 可选重排），问答与搜索共用。
    """
    pre_rank = [dict(h) for h in hits]  # 排序前候选快照（供 span 记录）
    reranker = create_reranker(config)
    documents = [h["text"] for h in hits]
    n = top_n or len(documents)
    scored = await reranker.arerank(query, documents, top_n=n)
    for idx, rel_score in scored:
        if 0 <= idx < len(hits):
            hits[idx]["score"] = round(float(rel_score), 4)
    hits.sort(key=lambda x: x["score"], reverse=True)

    get_client().update_current_span(
        metadata={"rerank_model": config.get("model"), "top_n": n},
        input=format_retrieval_observation(pre_rank),
        output=format_retrieval_observation(hits),
    )
    return hits


async def retrieve_and_rank(
    query: str,
    kb_id: str,
    top_k: int = 5,
    similarity_threshold: float = 0.2,
    rerank_config: dict | None = None,
    rerank_threshold: float = 0.8,
    retrieval_methods: list[str] | None = None,
    expand_query: Callable[[str], str | list[str]] | None = None,
    query_rewrite_config: dict | None = None,
    query_rewrite_history: list[dict] | None = None,
) -> list[dict[str, Any]]:
    """单知识库检索统一入口：多路召回（dense/sparse/fulltext）+ RRF 融合 + 可选重排 + 阈值过滤。

    Args:
        query: 用户问题或检索词
        kb_id: 目标知识库 ID
        top_k: 每路召回候选数量（也是无重排时的返回条数上限）
        similarity_threshold: 稠密路相似度阈值（IP 分数）；sparse/fulltext 无阈值语义
        rerank_config: 重排模型配置，为 None 时跳过重排
        rerank_threshold: 有重排时按重排分数过滤的阈值
        retrieval_methods: 检索策略列表（dense/sparse/fulltext，可多选），为 None 时默认纯稠密 ["dense"]
        expand_query: fulltext 路的查询扩展函数（词库同义词 OR 扩展），为 None 时原词直查
        query_rewrite_config: LLM 改写配置，为 None 时不改写；有配置时先改写 query 再召回
            （改写只服务召回，重排仍用原 query 判断相关性）
        query_rewrite_history: 改写时携带的最近对话轮次 [{"role", "content"}, ...]（仅问答传入）

    Returns:
        RRF 融合（单选时直返）并按分数降序的结果列表:
        [{score, text, doc_id, file_name, page_number, chunk_index, kb_id, ...}, ...]

    单路召回失败（如 ES 未部署）降级为空并记日志，不阻断整体检索。
    """
    methods = list(retrieval_methods) if retrieval_methods else ["dense"]
    invalid = set(methods) - set(RETRIEVAL_METHODS)
    if invalid:
        raise ValueError(f"不支持的检索方式: {sorted(invalid)}")

    # ===== 0. 可选 Query 改写（失败/空输出由 rewrite_query 内部回退原词） =====
    original_query = query
    if query_rewrite_config:
        query = await rewrite_query(query, query_rewrite_config, history=query_rewrite_history)

    # ===== 1. 各路独立召回（单路失败降级为空） =====
    ranked_lists: list[list[dict]] = []
    for m in methods:
        if m in ("dense", "sparse"):
            try:
                if m == "dense":
                    threshold = (0.2 if rerank_config else similarity_threshold)
                    hits = await retrieve(
                        query=query, kb_id=kb_id, top_k=top_k,
                        similarity_threshold=threshold, retrieval_method=m,
                    )
                else:
                    # sparse 分数非 [0,1]，无阈值语义
                    hits = await retrieve(
                        query=query, kb_id=kb_id, top_k=top_k, retrieval_method=m,
                    )
            except Exception as e:
                logger.warning("问答 %s 路检索失败，降级为空: %s", m, e)
                hits = []
        else:  # fulltext（ES + IK 全文检索）
            try:
                expanded = expand_query(query) if expand_query else query
                hits = await es_store.es_search(
                    expanded, [kb_id],
                    top_k=top_k * settings.retrieval_es_top_k_factor,
                    highlight=False,
                )
            except Exception as e:
                logger.warning("问答 fulltext 路检索失败，降级为空: %s", e)
                hits = []
        for h in hits:
            h.setdefault("kb_id", kb_id)
        ranked_lists.append(hits)

    # ===== 2. 单选直返 / 多选 RRF 融合 =====
    if len(ranked_lists) == 1:
        all_hits = ranked_lists[0]
        all_hits.sort(key=lambda x: x["score"], reverse=True)
    else:
        all_hits = rrf_merge(ranked_lists, k=settings.retrieval_rrf_k)

    # ===== 3. 可选重排 + 阈值过滤（重排用改写前的原查询判断相关性） =====
    if rerank_config and all_hits:
        all_hits = await rerank(original_query, all_hits, rerank_config)
        all_hits = [h for h in all_hits if h["score"] >= rerank_threshold]
    return all_hits
