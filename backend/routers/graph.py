"""
图谱 API — 构建管理（全量/单文件/清除/状态）+ 图检索（stats/search/entities）。

图谱功能未启用（GRAPH_ENABLED=false）时统一返回 503 友好提示。
"""
from fastapi import APIRouter, HTTPException, Query

from core.neo4j_store import GraphDisabledError
from models.graph_schemas import (
    EntityDetailResponse,
    GraphBuildResponse,
    GraphExpandRequest,
    GraphSearchRequest,
    GraphSearchResponse,
    GraphStatsResponse,
)
from services import document_service, graph_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}", tags=["知识图谱"])


def _graph_disabled_handler(exc: GraphDisabledError):
    return HTTPException(status_code=503, detail=str(exc))


# ============================================================
# 图谱管理
# ============================================================

@router.get("/graph/stats", response_model=GraphStatsResponse)
async def graph_stats(kb_id: str):
    """图谱概要统计（实体/关系数、类型分布、构建进度）。"""
    try:
        return await graph_service.get_graph_stats(kb_id)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)


@router.get("/graph")
async def graph_data(
    kb_id: str,
    limit: int = 150,
    offset: int = 0,
    exclude_names: list[str] = Query(default_factory=list),
):
    """图谱数据（ECharts 渲染，按 degree 降序分页；exclude_names=已加载节点名，增量加载）。"""
    try:
        return await graph_service.get_graph_data(kb_id, limit, offset, exclude_names)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)


@router.post("/graph/build", response_model=GraphBuildResponse, status_code=202)
async def build_kb_graph(kb_id: str):
    """全量构建知识库图谱（异步：清空后逐文件构建）。"""
    try:
        position = await graph_service.build_kb_task(kb_id)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    logger.info("图谱全量构建已入队: kb_id=%s, position=%d", kb_id, position)
    return {"id": kb_id, "status": "queued", "queue_position": position}


@router.delete("/graph", status_code=204)
async def clear_kb_graph(kb_id: str):
    """清空整个知识库图谱。"""
    try:
        await graph_service.clear_kb_graph(kb_id)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    logger.info("知识库图谱已清空: kb_id=%s", kb_id)


# ============================================================
# 单文件图谱操作
# ============================================================

@router.post("/documents/{doc_id}/graph/build", response_model=GraphBuildResponse, status_code=202)
async def build_doc_graph(kb_id: str, doc_id: str):
    """单文件增量构建图谱（异步，幂等）。"""
    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc["status"] != "completed":
        raise HTTPException(status_code=400, detail="文档尚未解析完成，无法构建图谱")
    if doc["graph_status"] == "building":
        raise HTTPException(status_code=400, detail="文档图谱正在构建中")
    try:
        position = await graph_service.build_file_task(kb_id, doc_id)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    logger.info("单文件图谱构建已入队: doc_id=%s, position=%d", doc_id, position)
    return {"id": doc_id, "status": "queued", "queue_position": position}


@router.delete("/documents/{doc_id}/graph", status_code=204)
async def clear_doc_graph(kb_id: str, doc_id: str):
    """清除单文件图谱。"""
    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        await graph_service.clear_file_graph(kb_id, doc_id)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    logger.info("单文件图谱已清除: doc_id=%s", doc_id)


@router.get("/documents/{doc_id}/graph/status")
async def doc_graph_status(kb_id: str, doc_id: str):
    """单文件图谱构建状态。"""
    doc = await document_service.get_document(kb_id, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "doc_id": doc_id,
        "graph_status": doc["graph_status"],
        "graph_error_message": doc["graph_error_message"],
        "graph_built_at": doc["graph_built_at"],
        "graph_duration_ms": doc["graph_duration_ms"],
    }


# ============================================================
# 图检索
# ============================================================

@router.post("/graph/search", response_model=GraphSearchResponse)
async def graph_search(kb_id: str, req: GraphSearchRequest):
    """子图检索：词库匹配入口实体 → 邻居展开 → chunk 级证据。"""
    try:
        return await graph_service.search_subgraph(
            kb_id, req.query, req.max_hops, req.limit
        )
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)


@router.post("/graph/expand")
async def expand_entity(kb_id: str, req: GraphExpandRequest):
    """分级展开：返回指定实体 1-hop 邻居子图（逐层浏览，避免全量渲染）。"""
    try:
        result = await graph_service.expand_node(kb_id, req.name, req.limit)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    if result is None:
        raise HTTPException(status_code=404, detail=f"实体不存在: {req.name}")
    return result


@router.get("/graph/entities", response_model=EntityDetailResponse)
async def entity_detail(kb_id: str, name: str):
    """实体详情：属性 + 1-hop 邻居 + chunk 证据（name 用 query 参数，实体名含特殊字符不破坏 URL）。"""
    try:
        result = await graph_service.get_entity_detail(kb_id, name)
    except GraphDisabledError as e:
        raise _graph_disabled_handler(e)
    if result is None:
        raise HTTPException(status_code=404, detail=f"实体不存在: {name}")
    return result
