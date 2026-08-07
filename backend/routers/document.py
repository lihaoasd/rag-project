"""
文档管理 API。
"""
from fastapi import APIRouter, File, HTTPException, UploadFile

from models.schemas import DocumentResponse
from services import document_service
from utils.file_utils import validate_file_type

router = APIRouter(prefix="/api/knowledge-bases/{kb_id}/documents", tags=["文档管理"])


@router.post("", response_model=DocumentResponse, status_code=201)
async def upload_document(kb_id: str, file: UploadFile = File(...)):
    """上传文档到指定知识库"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="未提供文件")

    # 格式校验
    if not validate_file_type(file.filename):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式。支持: pdf, docx, doc",
        )

    try:
        result = await document_service.upload_document(kb_id, file)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=list[DocumentResponse])
async def list_documents(kb_id: str):
    """获取知识库下的文档列表"""
    return await document_service.list_documents(kb_id)


@router.delete("/{doc_id}", status_code=204)
async def delete_document(kb_id: str, doc_id: str):
    """删除文档及向量数据"""
    success = await document_service.delete_document(kb_id, doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="文档不存在")
