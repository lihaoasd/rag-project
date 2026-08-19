"""
检索引擎 — llama_index VectorStoreIndex + Retriever + SimilarityPostprocessor。
"""
from typing import Any

from llama_index.core import VectorStoreIndex
from llama_index.core.postprocessor import SimilarityPostprocessor
from llama_index.core.schema import QueryBundle
from llama_index.core.vector_stores.types import VectorStoreQueryMode
from langfuse import get_client, observe

from config import settings
from core.model_factory import create_embedding, create_reranker, resolve_model_config
from core.observability import format_retrieval_observation
from core.vector_store import get_vector_store
from utils.logging import get_logger

logger = get_logger(__name__)


def _resolve_retrieval_method(retrieval_method: str | None) -> bool:
    """把检索方式参数解析为是否启用混合检索。"""
    if retrieval_method is None:
        return settings.retrieval_hybrid_enabled
    if retrieval_method == "vector":
        return False
    if retrieval_method == "hybrid":
        return True
    raise ValueError(f"不支持的检索方式: {retrieval_method}")


def _resolve_hybrid_ranker(
    hybrid_ranker: str | None,
    hybrid_weights: list[float] | None = None,
) -> tuple[str, dict]:
    """把融合策略参数解析为 MilvusVectorStore 支持的 ranker 配置。"""
    if hybrid_ranker in (None, "rrf"):
        return "RRFRanker", {}
    if hybrid_ranker == "weighted":
        weights = hybrid_weights if hybrid_weights is not None else [1.0, 1.0]
        if len(weights) != 2:
            raise ValueError("WeightedRanker 需要 [向量权重, 关键词权重] 两个权重")
        return "WeightedRanker", {"weights": weights}
    raise ValueError(f"不支持的混合检索融合策略: {hybrid_ranker}")


@observe(as_type="retriever", capture_input=False, capture_output=False)
async def retrieve(
    query: str,
    kb_id: str,
    top_k: int = 5,
    similarity_threshold: float = 0.6,
    retrieval_method: str | None = None,
    hybrid_ranker: str | None = None,
    hybrid_weights: list[float] | None = None,
) -> list[dict[str, Any]]:
    """在指定知识库中执行语义检索。

    Args:
        query: 检索关键词或自然语言描述
        kb_id: 目标知识库 ID
        top_k: 返回的最相关结果数
        similarity_threshold: 相似度阈值（IP 分数），低于此分数丢弃
        retrieval_method: 检索方式：vector/hybrid，为空则沿用服务端配置
        hybrid_ranker: 混合检索融合策略：rrf/weighted，仅 hybrid 生效
        hybrid_weights: WeightedRanker 的 [向量权重, 关键词权重]

    Returns:
        按分数降序排列的结果列表:
        [{score, text, doc_id, file_name, page_number, chunk_index}, ...]
    """
    hybrid = _resolve_retrieval_method(retrieval_method)
    if hybrid:
        ranker_name, ranker_params = _resolve_hybrid_ranker(
            hybrid_ranker, hybrid_weights
        )
    else:
        ranker_name, ranker_params = "RRFRanker", {}
    retrieval_metadata = {
        "method": "hybrid" if hybrid else "vector",
        "kb_id": kb_id,
        "top_k": top_k,
        "similarity_threshold": similarity_threshold,
        "hybrid_ranker": ranker_name,
        "hybrid_ranker_params": ranker_params,
    }

    # 1. resolve Embedding 配置（维度 + client）
    embed_config = await resolve_model_config(kb_id, "embedding")
    dim = (embed_config.get("params") or {}).get("dimension") or settings.embedding_dim
    embed_model = create_embedding(embed_config)

    # 2. 创建 vector_store + index
    vector_store = get_vector_store(
        kb_id,
        dim,
        hybrid_ranker=ranker_name,
        hybrid_ranker_params=ranker_params,
    )

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
    query_mode = VectorStoreQueryMode.HYBRID if hybrid else VectorStoreQueryMode.DEFAULT
    retriever = index.as_retriever(
        similarity_top_k=top_k, vector_store_query_mode=query_mode
    )

    # 4. 查询向量化
    query_embedding = await embed_model.aget_query_embedding(query)
    query_bundle = QueryBundle(query_str=query, embedding=query_embedding)

    # 5. 检索
    nodes = await retriever.aretrieve(query_bundle)

    # 6. 后处理：相似度阈值过滤（混合模式融合分数非 [0,1]，仅稠密模式适用）
    if hybrid:
        filtered_nodes = nodes
    else:
        processor = SimilarityPostprocessor(similarity_cutoff=similarity_threshold)
        filtered_nodes = processor.postprocess_nodes(nodes)

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


@observe(as_type="retriever", capture_input=False, capture_output=False)
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
) -> list[dict[str, Any]]:
    """单知识库检索统一入口：向量召回 + 可选重排 + rerank 后阈值过滤。

    rerank_config 为 None 时退化为纯向量召回；问答与搜索共用同一检索链路。
    rerank_threshold 仅在有 rerank 时生效，过滤掉 rerank 分数低于该阈值的命中。
    """
    hits = await retrieve(
        query=query,
        kb_id=kb_id,
        top_k=top_k,
        similarity_threshold=rerank_config and 0.2 or similarity_threshold,
    )
    if rerank_config and hits:
        hits = await rerank(query, hits, rerank_config)
        hits = [h for h in hits if h["score"] >= rerank_threshold]
    return hits
