"""
Pydantic Schema 验证单元测试。
"""
import pytest
from pydantic import ValidationError

from models.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseListResponse,
    DocumentResponse,
    DocumentListResponse,
    SearchRequest,
    SearchResultItem,
    SearchResponse,
    ChatRequest,
    ChatSourceItem,
    ChatSSEEvent,
    ChunkResponse,
    ChunkDetailResponse,
    ChunkListResponse,
    ChunkUpdateRequest,
    RechunkRequest,
    ErrorResponse,
    HealthResponse,
)


# ============================================================
# KnowledgeBaseCreate
# ============================================================
class TestKnowledgeBaseCreate:
    def test_valid_minimal(self):
        kb = KnowledgeBaseCreate(name="测试知识库")
        assert kb.name == "测试知识库"
        assert kb.description is None

    def test_valid_with_description(self):
        kb = KnowledgeBaseCreate(name="测试知识库", description="这是一个描述")
        assert kb.name == "测试知识库"
        assert kb.description == "这是一个描述"

    def test_missing_name_raises_error(self):
        with pytest.raises(ValidationError):
            KnowledgeBaseCreate()

    def test_empty_name_allowed_by_pydantic(self):
        """空字符串 name 默认允许（除非显示指定 min_length）。"""
        kb = KnowledgeBaseCreate(name="")
        assert kb.name == ""


# ============================================================
# KnowledgeBaseResponse
# ============================================================
class TestKnowledgeBaseResponse:
    def test_valid_response(self):
        kb = KnowledgeBaseResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            name="测试知识库",
            description="描述",
            document_count=5,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert kb.id == "550e8400-e29b-41d4-a716-446655440000"
        assert kb.document_count == 5

    def test_description_nullable(self):
        kb = KnowledgeBaseResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            name="测试知识库",
            description=None,
            document_count=0,
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )
        assert kb.description is None

    def test_missing_required_fields_raises_error(self):
        with pytest.raises(ValidationError):
            KnowledgeBaseResponse(name="测试知识库")


# ============================================================
# KnowledgeBaseListResponse
# ============================================================
class TestKnowledgeBaseListResponse:
    def test_valid_empty_list(self):
        resp = KnowledgeBaseListResponse(items=[], total=0)
        assert resp.items == []
        assert resp.total == 0

    def test_valid_with_items(self):
        items = [
            KnowledgeBaseResponse(
                id="550e8400-e29b-41d4-a716-446655440000",
                name="KB1",
                description=None,
                document_count=0,
                created_at="2024-01-01T00:00:00",
                updated_at="2024-01-01T00:00:00",
            )
        ]
        resp = KnowledgeBaseListResponse(items=items, total=1)
        assert len(resp.items) == 1
        assert resp.total == 1


# ============================================================
# DocumentResponse
# ============================================================
class TestDocumentResponse:
    def test_valid_uploaded_doc(self):
        doc = DocumentResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            file_name="test.pdf",
            file_size=1024,
            file_type="pdf",
            page_count=None,
            status="uploaded",
            error_message=None,
            chunk_count=None,
            created_at="2024-01-01T00:00:00",
        )
        assert doc.status == "uploaded"
        assert doc.file_name == "test.pdf"

    def test_valid_completed_doc(self):
        doc = DocumentResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            file_name="test.pdf",
            file_size=2048,
            file_type="pdf",
            page_count=10,
            status="completed",
            error_message=None,
            chunk_count=50,
            created_at="2024-01-01T00:00:00",
        )
        assert doc.status == "completed"
        assert doc.page_count == 10
        assert doc.chunk_count == 50

    def test_valid_failed_doc(self):
        doc = DocumentResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            file_name="test.pdf",
            file_size=0,
            file_type="pdf",
            page_count=None,
            status="failed",
            error_message="解析失败",
            chunk_count=None,
            created_at="2024-01-01T00:00:00",
        )
        assert doc.status == "failed"
        assert doc.error_message == "解析失败"


# ============================================================
# SearchRequest
# ============================================================
class TestSearchRequest:
    def test_valid_minimal(self):
        req = SearchRequest(query="测试查询")
        assert req.query == "测试查询"
        assert req.top_k == 10  # 默认值
        assert req.similarity_threshold == 0.6  # 默认值
        assert req.retrieval_methods is None  # 默认值
        assert req.rerank_top_n == 5  # 默认值

    def test_valid_retrieval_methods(self):
        req = SearchRequest(query="测试查询", retrieval_methods=["dense"])
        assert req.retrieval_methods == ["dense"]

        req = SearchRequest(query="测试查询", retrieval_methods=["sparse"])
        assert req.retrieval_methods == ["sparse"]

        req = SearchRequest(query="测试查询", retrieval_methods=["fulltext"])
        assert req.retrieval_methods == ["fulltext"]

        req = SearchRequest(query="测试查询", retrieval_methods=["dense", "sparse", "fulltext"])
        assert req.retrieval_methods == ["dense", "sparse", "fulltext"]

    def test_valid_highlight_default(self):
        req = SearchRequest(query="测试查询")
        assert req.highlight is True  # 默认返回高亮

        req = SearchRequest(query="测试查询", highlight=False)
        assert req.highlight is False

    def test_invalid_retrieval_method_raises_error(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="测试查询", retrieval_methods=["bm25"])

        with pytest.raises(ValidationError):
            SearchRequest(query="测试查询", retrieval_methods=["dense", "bm25"])

    def test_valid_custom_params(self):
        req = SearchRequest(
            query="测试查询",
            top_k=5,
            similarity_threshold=0.8,
            retrieval_methods=["dense", "fulltext"],
            rerank_top_n=30,
        )
        assert req.top_k == 5
        assert req.similarity_threshold == 0.8
        assert req.retrieval_methods == ["dense", "fulltext"]
        assert req.rerank_top_n == 30

    def test_missing_query_raises_error(self):
        with pytest.raises(ValidationError):
            SearchRequest()

    def test_top_k_out_of_range(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=0)

        with pytest.raises(ValidationError):
            SearchRequest(query="test", top_k=51)

    def test_similarity_threshold_out_of_range(self):
        with pytest.raises(ValidationError):
            SearchRequest(query="test", similarity_threshold=-0.1)

        with pytest.raises(ValidationError):
            SearchRequest(query="test", similarity_threshold=1.1)

    def test_rerank_top_n_out_of_range(self):
        # 边界 0 与 30 合法
        assert SearchRequest(query="test", rerank_top_n=0).rerank_top_n == 0
        assert SearchRequest(query="test", rerank_top_n=30).rerank_top_n == 30

        with pytest.raises(ValidationError):
            SearchRequest(query="test", rerank_top_n=-1)

        with pytest.raises(ValidationError):
            SearchRequest(query="test", rerank_top_n=31)


# ============================================================
# SearchResultItem
# ============================================================
class TestSearchResultItem:
    def test_valid_item(self):
        item = SearchResultItem(
            rank=1,
            score=0.95,
            text="相关文档内容",
            doc_id="550e8400-e29b-41d4-a716-446655440000",
            file_name="test.pdf",
            page_number=3,
            chunk_index=5,
        )
        assert item.rank == 1
        assert item.score == 0.95

    def test_missing_fields_raises_error(self):
        with pytest.raises(ValidationError):
            SearchResultItem(rank=1)


# ============================================================
# SearchResponse
# ============================================================
class TestSearchResponse:
    def test_valid_response(self):
        resp = SearchResponse(query="test", total=0, results=[])
        assert resp.query == "test"
        assert resp.total == 0
        assert resp.results == []


# ============================================================
# ChatRequest
# ============================================================
class TestChatRequest:
    def test_valid_without_session(self):
        req = ChatRequest(message="你好")
        assert req.message == "你好"
        assert req.session_id is None

    def test_valid_with_session(self):
        req = ChatRequest(
            message="你好",
            session_id="550e8400-e29b-41d4-a716-446655440000",
        )
        assert req.message == "你好"
        assert req.session_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_missing_message_raises_error(self):
        with pytest.raises(ValidationError):
            ChatRequest()


# ============================================================
# ChatSSEEvent
# ============================================================
class TestChatSSEEvent:
    def test_token_event(self):
        evt = ChatSSEEvent(type="token", content="你好")
        assert evt.type == "token"
        assert evt.content == "你好"

    def test_sources_event(self):
        sources = [
            ChatSourceItem(
                doc_id="550e8400-e29b-41d4-a716-446655440000",
                file_name="test.pdf",
                page_number=1,
                chunk_index=0,
                score=0.95,
                text="文档内容",
            )
        ]
        evt = ChatSSEEvent(type="sources", sources=sources)
        assert evt.type == "sources"
        assert len(evt.sources) == 1

    def test_error_event(self):
        evt = ChatSSEEvent(type="error", message="内部错误")
        assert evt.type == "error"
        assert evt.message == "内部错误"

    def test_done_event(self):
        evt = ChatSSEEvent(type="done")
        assert evt.type == "done"


# ============================================================
# ChunkUpdateRequest
# ============================================================
class TestChunkUpdateRequest:
    def test_valid(self):
        req = ChunkUpdateRequest(text="新的切片文本")
        assert req.text == "新的切片文本"

    def test_empty_text_raises_error(self):
        with pytest.raises(ValidationError):
            ChunkUpdateRequest(text="")


# ============================================================
# RechunkRequest
# ============================================================
class TestRechunkRequest:
    def test_defaults(self):
        req = RechunkRequest()
        assert req.chunk_size == 512
        assert req.chunk_overlap == 64

    def test_custom_values(self):
        req = RechunkRequest(chunk_size=1024, chunk_overlap=128)
        assert req.chunk_size == 1024
        assert req.chunk_overlap == 128

    def test_chunk_size_out_of_range(self):
        with pytest.raises(ValidationError):
            RechunkRequest(chunk_size=100)  # < 128

        with pytest.raises(ValidationError):
            RechunkRequest(chunk_size=5000)  # > 4096

    def test_chunk_overlap_out_of_range(self):
        with pytest.raises(ValidationError):
            RechunkRequest(chunk_overlap=-1)  # < 0

        with pytest.raises(ValidationError):
            RechunkRequest(chunk_overlap=600)  # > 512


# ============================================================
# ChunkResponse / ChunkDetailResponse / ChunkListResponse
# ============================================================
class TestChunkResponses:
    def test_chunk_response_preview(self):
        c = ChunkResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            chunk_index=0,
            page_number=1,
            char_count=512,
            text_preview="前200字预览...",
            created_at="2024-01-01T00:00:00",
        )
        assert c.chunk_index == 0
        assert c.text_preview == "前200字预览..."

    def test_chunk_detail_response(self):
        c = ChunkDetailResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            doc_id="550e8400-e29b-41d4-a716-446655440001",
            chunk_index=0,
            page_number=1,
            char_count=1024,
            text_content="完整文本内容",
            milvus_pk="pk_abc123",
            created_at="2024-01-01T00:00:00",
        )
        assert c.text_content == "完整文本内容"
        assert c.milvus_pk == "pk_abc123"

    def test_chunk_detail_response_nullable_milvus_pk(self):
        c = ChunkDetailResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            doc_id="550e8400-e29b-41d4-a716-446655440001",
            chunk_index=0,
            page_number=1,
            char_count=1024,
            text_content="完整文本内容",
            milvus_pk=None,
            created_at="2024-01-01T00:00:00",
        )
        assert c.milvus_pk is None

    def test_chunk_list_response(self):
        items = [
            ChunkResponse(
                id="550e8400-e29b-41d4-a716-446655440000",
                chunk_index=0,
                page_number=1,
                char_count=512,
                text_preview="预览...",
                created_at="2024-01-01T00:00:00",
            )
        ]
        resp = ChunkListResponse(items=items, total=1, page=1, page_size=20)
        assert resp.total == 1
        assert resp.page == 1
        assert resp.page_size == 20


# ============================================================
# ErrorResponse / HealthResponse
# ============================================================
class TestCommonSchemas:
    def test_error_response(self):
        err = ErrorResponse(detail="错误描述")
        assert err.detail == "错误描述"

    def test_health_response(self):
        h = HealthResponse(status="healthy", version="0.1.0")
        assert h.status == "healthy"
        assert h.version == "0.1.0"
