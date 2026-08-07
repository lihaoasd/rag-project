"""
文档管理业务逻辑。
Phase 1: 返回占位数据，完整实现后续补全。
"""
from datetime import datetime, timezone
from fastapi import UploadFile


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def upload_document(kb_id: str, file: UploadFile) -> dict:
    """上传文档到指定知识库。Phase 1 stub。"""
    return {
        "id": "doc_placeholder",
        "file_name": file.filename or "unknown",
        "file_size": file.size or 0,
        "file_type": "pdf",
        "page_count": None,
        "status": "processing",
        "error_message": None,
        "chunk_count": None,
        "created_at": _now(),
    }


async def list_documents(kb_id: str) -> list[dict]:
    """获取知识库下的文档列表。Phase 1 stub。"""
    return []


async def delete_document(kb_id: str, doc_id: str) -> bool:
    """删除文档及向量数据。Phase 1 stub。"""
    return True
