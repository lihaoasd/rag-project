"""
图谱 Schema — LLM 抽取输出校验 + 图检索请求/响应。
"""
from typing import Literal

from pydantic import BaseModel, Field


# ============================================================
# LLM 抽取输出校验（Schema 约束 JSON）
# ============================================================
ENTITY_TYPES = Literal[
    "ORGANIZATION", "PERSON", "PLACE", "EVENT", "TIME",
    "PROJECT", "PRODUCT", "QUANTITY", "CONCEPT",
]


class ExtractedEntity(BaseModel):
    name: str = Field(..., min_length=1, description="实体名称")
    type: ENTITY_TYPES = Field(..., description="实体类型")
    description: str = Field("", description="简短描述")


class ExtractedRelationship(BaseModel):
    source: str = Field(..., min_length=1, description="源实体名")
    target: str = Field(..., min_length=1, description="目标实体名")
    type: str = Field(..., min_length=1, description="关系类型（中标/合作/分包/…）")
    description: str = Field("", description="关系说明")


class ExtractionOutput(BaseModel):
    """一次 LLM 抽取的输出（一批 chunk 的实体 + 关系）。"""

    entities: list[ExtractedEntity] = Field(default_factory=list)
    relationships: list[ExtractedRelationship] = Field(default_factory=list)


# ============================================================
# 图检索请求 / 响应
# ============================================================
class GraphSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="检索词（实体名/别名/自然语言片段）")
    max_hops: int = Field(2, ge=1, le=3, description="邻居展开最大跳数")
    limit: int = Field(200, ge=1, le=1000, description="子图节点上限")


class GraphExpandRequest(BaseModel):
    """分级展开：以某实体为中心返回 1-hop 邻居子图。"""

    name: str = Field(..., min_length=1, description="实体名")
    limit: int = Field(100, ge=1, le=300, description="邻居节点上限")


class EntityListItem(BaseModel):
    """实体分页列表行。"""

    name: str = Field(..., description="实体名")
    type: str = Field(..., description="实体类型（Organization/Project/…）")
    description: str = Field("", description="实体描述")
    relation_count: int = Field(0, description="业务关系数（不含 提及 边）")
    chunk_count: int = Field(0, description="来源 chunk 数（mentioned_chunk_ids 长度）")


class EntityListResponse(BaseModel):
    total: int = Field(0, description="符合条件实体总数")
    page: int = Field(1)
    page_size: int = Field(50)
    items: list[EntityListItem] = Field(default_factory=list)


class EntityUpdateRequest(BaseModel):
    """实体编辑：old_* 定位节点，name/type/description 为新值。"""

    old_name: str = Field(..., min_length=1, description="编辑前实体名")
    old_type: str = Field(..., min_length=1, description="编辑前实体类型")
    name: str = Field(..., min_length=1, description="新实体名")
    type: str = Field(..., min_length=1, description="新实体类型")
    description: str = Field("", description="新描述")


class GraphNode(BaseModel):
    id: str = Field(..., description="节点 ID（如 org_甲公司）")
    name: str = Field(..., description="实体名")
    type: str = Field(..., description="实体类型（Organization/Project/…）")
    degree: int = Field(0, description="节点度")


class GraphEdge(BaseModel):
    source: str = Field(..., description="源节点 ID")
    target: str = Field(..., description="目标节点 ID")
    type: str = Field(..., description="关系类型")


class ChunkEvidenceItem(BaseModel):
    """chunk 级证据：命中实体的来源切片。"""

    doc_id: str
    file_name: str = ""
    chunk_id: str
    chunk_index: int
    page_number: int
    text: str = Field(..., description="切片原文（前端高亮实体）")


class EntityEvidence(BaseModel):
    entity: str = Field(..., description="实体名")
    chunks: list[ChunkEvidenceItem] = Field(default_factory=list)


class GraphSearchResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    evidence: list[EntityEvidence] = Field(default_factory=list, description="chunk 级证据")


class GraphStatsResponse(BaseModel):
    kb_id: str
    graph_exists: bool = False
    stats: dict = Field(default_factory=dict, description="实体/关系数、类型分布")
    build: dict = Field(default_factory=dict, description="构建进度 doc_total/built/building/failed/none")


class EntityDetailResponse(BaseModel):
    entity: dict = Field(default_factory=dict, description="实体属性（name/type/description/degree）")
    neighbors: list[dict] = Field(default_factory=list, description="1-hop 邻居（含关系类型/方向）")
    evidence: list[ChunkEvidenceItem] = Field(default_factory=list, description="chunk 级证据")


class GraphBuildResponse(BaseModel):
    """构建/清除任务响应（与解析队列一致风格）"""

    id: str
    status: str
    queue_position: int = 0
