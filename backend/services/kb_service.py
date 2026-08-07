"""
知识库业务逻辑。
Phase 1: 返回占位数据，完整实现后续补全。
"""
from datetime import datetime, timezone

from utils.logging import get_logger

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_knowledge_base(name: str, description: str | None = None) -> dict:
    """创建知识库。Phase 1 stub。"""
    logger.debug("create_knowledge_base stub: name=%s", name)
    return {
        "id": "kb_placeholder",
        "name": name,
        "description": description,
        "document_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }


async def list_knowledge_bases() -> list[dict]:
    """获取知识库列表。Phase 1 stub。"""
    logger.debug("list_knowledge_bases stub")
    return []


async def get_knowledge_base(kb_id: str) -> dict | None:
    """获取知识库详情。Phase 1 stub。"""
    logger.debug("get_knowledge_base stub: kb_id=%s", kb_id)
    return {
        "id": kb_id,
        "name": "示例知识库",
        "description": "这是一个占位示例",
        "document_count": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }


async def delete_knowledge_base(kb_id: str) -> bool:
    """删除知识库。Phase 1 stub。"""
    logger.debug("delete_knowledge_base stub: kb_id=%s", kb_id)
    return True
