"""
ES 管理 API — 对账（只读体检）与重建（从 PG 重灌 ES）。
"""
from fastapi import APIRouter

from services import es_admin_service
from utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["ES 管理"])


@router.get("/api/es/status")
async def es_status(page: int = 1, page_size: int = 20):
    """对账：各知识库 PG 切片数 vs ES 文档数差异（只读，不修改数据）。"""
    return await es_admin_service.reconcile_status(page=page, page_size=page_size)


@router.post("/api/es/rebuild")
async def es_rebuild(kb_id: str | None = None):
    """从 PG 全量重建 ES（指定知识库或全部；修复双写漂移）。"""
    return await es_admin_service.rebuild_es(kb_id)
