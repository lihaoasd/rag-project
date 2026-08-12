"""
Pydantic 请求/响应 Schema — API 数据传输对象。
"""
from datetime import datetime
from pydantic import BaseModel, Field


# ============================================================
# 知识库
# ============================================================
class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., description="知识库名称")
    description: str | None = Field(None, description="知识库描述")


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str | None
    document_count: int
    created_at: str
    updated_at: str


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseResponse]
    total: int


# ============================================================
# 文档
# ============================================================
class DocumentResponse(BaseModel):
    id: str
    file_name: str
    file_size: int
    file_type: str
    page_count: int | None
    status: str
    error_message: str | None
    chunk_count: int | None
    created_at: str


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int


class ParseResponse(BaseModel):
    id: str
    status: str
    queue_position: int


# ============================================================
# 检索
# ============================================================
class SearchRequest(BaseModel):
    query: str = Field(..., description="检索关键词或自然语言描述")
    top_k: int = Field(10, ge=1, le=50, description="向量检索候选数量")
    similarity_threshold: float = Field(0.6, ge=0, le=1, description="相似度阈值")
    offset: int = Field(0, ge=0, description="分页偏移")
    limit: int = Field(20, ge=1, le=100, description="每页条数")


class SearchResultItem(BaseModel):
    rank: int
    score: float
    text: str
    doc_id: str
    file_name: str
    page_number: int
    chunk_index: int


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResultItem]


# ============================================================
# 问答
# ============================================================
class ChatRequest(BaseModel):
    message: str = Field(..., description="用户问题")
    session_id: str | None = Field(None, description="会话ID，不传则自动创建新会话")


class ChatSourceItem(BaseModel):
    doc_id: str
    file_name: str
    page_number: int
    chunk_index: int
    score: float
    text: str


class ChatSSEEvent(BaseModel):
    type: str  # "token" | "sources" | "error" | "done"
    content: str | None = None
    sources: list[ChatSourceItem] | None = None
    message: str | None = None


# ============================================================
# 切片
# ============================================================
class ChunkResponse(BaseModel):
    """切片列表项（text 截断为前 200 字符预览）"""
    id: str
    chunk_index: int
    page_number: int
    char_count: int
    text_preview: str
    created_at: str


class ChunkDetailResponse(BaseModel):
    """切片详情（完整文本）"""
    id: str
    doc_id: str
    chunk_index: int
    page_number: int
    char_count: int
    text_content: str
    milvus_pk: str | None
    created_at: str


class ChunkListResponse(BaseModel):
    """分页切片列表"""
    items: list[ChunkResponse]
    total: int
    page: int
    page_size: int


class ChunkUpdateRequest(BaseModel):
    """编辑切片文本"""
    text: str = Field(..., min_length=1, description="新的切片文本内容")


class RechunkRequest(BaseModel):
    """重新切片参数"""
    chunk_size: int = Field(512, ge=128, le=4096, description="切片大小（tokens）")
    chunk_overlap: int = Field(64, ge=0, le=512, description="切片重叠（tokens）")


# ============================================================
# 通用
# ============================================================
class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str
