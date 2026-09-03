"""
Pydantic 请求/响应 Schema — API 数据传输对象。
"""
from datetime import datetime
from typing import Literal

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
    processed_chunk_count: int | None = None
    parse_duration_ms: int | None = None
    graph_status: str = "none"
    graph_error_message: str | None = None
    graph_built_at: str | None = None
    graph_duration_ms: int | None = None
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
    top_k: int = Field(10, ge=1, le=50, description="向量检索候选数量（也是无重排时的返回条数上限）")
    similarity_threshold: float = Field(0.6, ge=0, le=1, description="相似度阈值")
    kb_ids: list[str] | None = Field(None, description="知识库 ID 列表，支持多知识库检索")
    retrieval_methods: list[Literal["dense", "sparse", "fulltext", "graph"]] | None = Field(
        None,
        description="检索策略（可多选）：dense=稠密向量，sparse=稀疏向量(BM25)，fulltext=ES全文检索，graph=知识图谱证据；多选时按 RRF 融合，单选直接返回该路结果；不传则沿用服务端默认（retrieval_default_methods）",
    )
    highlight: bool = Field(True, description="是否返回高亮片段（fulltext 生效）")
    rerank_model_id: str | None = Field(None, description="排序模型配置 ID，为空则不做重排")
    rerank_top_n: int = Field(5, ge=0, le=30, description="进入排序模型的向量候选数量")


class SearchResultItem(BaseModel):
    rank: int
    score: float
    text: str
    doc_id: str
    file_name: str
    page_number: int
    chunk_index: int
    chunk_type: str = "text"
    source_chunk_index: int = -1
    kb_id: str = ""
    kb_name: str = ""
    highlight: str | None = Field(None, description="ES 高亮片段（含 <em> 标签），非 fulltext 为 null")
    graph_entity: str | None = Field(None, description="图谱检索来源实体名（graph 通道返回，非图检索为 null）")


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
    chunk_type: str = "text"
    source_chunk_index: int = -1
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
    chunk_type: str = "text"
    source_chunk_index: int = -1
    image_url: str | None = Field(None, description="图片 chunk 的预览地址（非图片 chunk 为 null）")
    image_urls: list[str] = Field(default_factory=list, description="chunk 关联图片的预览地址列表")
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
    metadata: dict = Field(default_factory=dict)
    image_url: str | None = Field(None, description="图片 chunk 的预览地址（非图片 chunk 为 null）")
    image_urls: list[str] = Field(default_factory=list, description="chunk 关联图片的预览地址列表")
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
# 提示词模板
# ============================================================
class PromptVariableDef(BaseModel):
    """变量定义"""
    name: str = Field(..., description="变量名（如 context_str）")
    label: str = Field(..., description="变量标签（如 检索上下文）")
    required: bool = Field(True, description="是否必填")
    description: str = Field("", description="变量描述")


class PromptTemplateCreate(BaseModel):
    """创建提示词模板请求"""
    name: str = Field(..., min_length=1, max_length=255, description="模板名称")
    description: str | None = Field(None, description="模板描述")
    scenario: str = Field(..., description="场景类型: rag_qa / entity_extraction / document_summary / custom")
    system_prompt: str = Field(..., min_length=1, description="系统提示词（含变量占位符）")
    user_prompt: str | None = Field(None, description="用户提示词模板（可选）")
    variables: list[PromptVariableDef] | None = Field(None, description="变量定义列表")
    is_active: bool = Field(False, description="是否激活")
    sort_order: int = Field(0, ge=0, description="排序序号")


class PromptTemplateUpdate(BaseModel):
    """更新提示词模板请求"""
    name: str | None = Field(None, min_length=1, max_length=255, description="模板名称")
    description: str | None = Field(None, description="模板描述")
    scenario: str | None = Field(None, description="场景类型")
    system_prompt: str | None = Field(None, min_length=1, description="系统提示词")
    user_prompt: str | None = Field(None, description="用户提示词模板")
    variables: list[PromptVariableDef] | None = Field(None, description="变量定义列表")
    is_active: bool | None = Field(None, description="是否激活")
    sort_order: int | None = Field(None, ge=0, description="排序序号")


class PromptTemplateResponse(BaseModel):
    """提示词模板响应"""
    id: str
    name: str
    description: str | None
    scenario: str
    system_prompt: str
    user_prompt: str | None
    variables: list[PromptVariableDef] | None
    is_active: bool
    is_default: bool
    sort_order: int
    created_at: str
    updated_at: str


class PromptTemplateListResponse(BaseModel):
    """提示词模板列表响应"""
    items: list[PromptTemplateResponse]
    total: int


class PromptPreviewRequest(BaseModel):
    """提示词预览请求"""
    template_id: str | None = Field(None, description="模板 ID（从 DB 读取模板内容）")
    system_prompt: str | None = Field(None, description="直接传入模板内容（与 template_id 二选一）")
    variables: dict[str, str] = Field(default_factory=dict, description="变量键值对")


class PromptPreviewResponse(BaseModel):
    """提示词预览响应"""
    rendered: str
    missing_variables: list[str] = Field(default_factory=list, description="缺失的必填变量")
    template_variables: list[PromptVariableDef] | None = Field(None, description="模板中定义的变量列表")


# ============================================================
# 模型配置
# ============================================================
class ModelConfigCreate(BaseModel):
    """创建模型配置请求"""
    name: str = Field(..., min_length=1, max_length=100, description="配置名称")
    model_type: str = Field(..., description="模型类型: llm/embedding/rerank/vision/ocr")
    platform: str = Field(..., min_length=1, max_length=50, description="平台标识")
    model: str = Field(..., min_length=1, max_length=100, description="模型名称")
    api_base: str | None = Field(None, description="API 地址")
    api_key: str | None = Field(None, description="API Key（或 ${ENV} 占位符）")
    params: dict | None = Field(None, description="类型特定参数")
    is_default: bool = Field(False, description="是否设为该类型全局默认")
    is_active: bool = Field(True, description="是否启用（停用后不参与解析与启动自检）")


class ModelConfigUpdate(BaseModel):
    """更新模型配置请求"""
    name: str | None = Field(None, min_length=1, max_length=100)
    model_type: str | None = Field(None)
    platform: str | None = Field(None, min_length=1, max_length=50)
    model: str | None = Field(None, min_length=1, max_length=100)
    api_base: str | None = Field(None)
    api_key: str | None = Field(None)
    params: dict | None = Field(None)
    is_default: bool | None = Field(None)
    is_active: bool | None = Field(None, description="是否启用")


class ModelConfigResponse(BaseModel):
    """模型配置响应"""
    id: str
    name: str
    model_type: str
    platform: str
    model: str
    api_base: str | None
    api_key: str | None
    params: dict | None
    is_default: bool
    is_active: bool = True
    created_at: str
    updated_at: str


class KBModelBindingSet(BaseModel):
    """知识库模型绑定请求（null 表示解绑回退默认）"""
    model_config_id: str | None = Field(None, description="模型配置 ID")


class ModelTestRequest(BaseModel):
    """模型连接测试请求（不落库）"""
    model_type: str = Field(..., description="模型类型: llm/embedding/rerank/vision/ocr")
    platform: str = Field(..., min_length=1, max_length=50, description="平台标识")
    model: str = Field(..., min_length=1, max_length=100, description="模型名称")
    api_base: str | None = Field(None, description="API 地址")
    api_key: str | None = Field(None, description="API Key（或 ${ENV} 占位符）")
    params: dict | None = Field(None, description="类型特定参数")


class ModelTestResponse(BaseModel):
    """模型连接测试响应"""
    success: bool
    message: str
    latency_ms: int | None = Field(None, description="请求耗时（毫秒）")
    detail: str | None = Field(None, description="额外信息（如向量维度 / 响应预览）")


# ============================================================
# 通用
# ============================================================
class ErrorResponse(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    version: str


# ============================================================
# 词库（lexicon）
# ============================================================
class LexiconTermCreate(BaseModel):
    term: str = Field(..., min_length=1, max_length=255, description="词条（标准词）")
    aliases: list[str] = Field(default_factory=list, description="别名/同义词列表")
    category: str = Field("", max_length=50, description="分类（自由文本，可空）")
    enabled: bool = Field(True, description="启用状态")


class LexiconTermUpdate(BaseModel):
    term: str | None = Field(None, min_length=1, max_length=255)
    aliases: list[str] | None = Field(None)
    category: str | None = Field(None, max_length=50)
    enabled: bool | None = Field(None)


class LexiconTermResponse(BaseModel):
    id: str
    term: str
    aliases: list[str]
    category: str
    enabled: bool
    created_at: str
    updated_at: str


class LexiconTermListResponse(BaseModel):
    items: list[LexiconTermResponse]
    total: int


class LexiconStatsResponse(BaseModel):
    total: int
    enabled: int
    categories: dict[str, int]


class LexiconImportRequest(BaseModel):
    """CSV 导入：每行 `词条,别名1;别名2,分类`（表头可选）"""

    content: str = Field(..., description="CSV 文本内容（UTF-8）")


class LexiconImportResponse(BaseModel):
    imported: int
    skipped: int
    errors: list[str] = Field(default_factory=list)
