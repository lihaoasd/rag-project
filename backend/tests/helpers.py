"""
测试共享常量和 Mock 类。
"""
import uuid
from unittest.mock import AsyncMock, MagicMock


def uid() -> str:
    return str(uuid.uuid4())


KB_ID = uid()
DOC_ID = uid()
CHUNK_ID = uid()
CONV_ID = uid()
MSG_ID = uid()


# ============================================================
# Mock ORM 对象
# ============================================================
class MockKB:
    """模拟 KnowledgeBase ORM 对象。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", KB_ID)
        self.name = kwargs.get("name", "测试知识库")
        self.description = kwargs.get("description", "测试描述")
        self.document_count = kwargs.get("document_count", 0)
        self.created_at = kwargs.get("created_at", None)
        self.updated_at = kwargs.get("updated_at", None)


class MockDocument:
    """模拟 Document ORM 对象。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", DOC_ID)
        self.kb_id = kwargs.get("kb_id", KB_ID)  # VARCHAR 列
        self.file_name = kwargs.get("file_name", "test.pdf")
        self.file_size = kwargs.get("file_size", 1024)
        self.file_type = kwargs.get("file_type", "pdf")
        self.object_key = kwargs.get("object_key", f"{KB_ID}/{DOC_ID}.pdf")
        self.page_count = kwargs.get("page_count", None)
        self.status = kwargs.get("status", "uploaded")
        self.error_message = kwargs.get("error_message", None)
        self.chunk_count = kwargs.get("chunk_count", None)
        self.processed_chunk_count = kwargs.get("processed_chunk_count", None)
        self.parse_duration_ms = kwargs.get("parse_duration_ms", None)
        self.created_at = kwargs.get("created_at", None)
        self.updated_at = kwargs.get("updated_at", None)


class MockChunk:
    """模拟 DocumentChunk ORM 对象。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", CHUNK_ID)
        self.doc_id = kwargs.get("doc_id", DOC_ID)  # VARCHAR 列
        self.kb_id = kwargs.get("kb_id", KB_ID)  # VARCHAR 列
        self.chunk_index = kwargs.get("chunk_index", 0)
        self.page_number = kwargs.get("page_number", 1)
        self.char_count = kwargs.get("char_count", 100)
        self.text_content = kwargs.get("text_content", "测试切片文本内容")
        self.milvus_pk = kwargs.get("milvus_pk", "pk_12345")
        self.chunk_metadata = kwargs.get("chunk_metadata", {})
        self.created_at = kwargs.get("created_at", None)


class MockConversation:
    """模拟 Conversation ORM 对象。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", CONV_ID)
        self.kb_id = kwargs.get("kb_id", KB_ID)  # VARCHAR 列
        self.title = kwargs.get("title", "测试会话")
        self.message_count = kwargs.get("message_count", 0)
        self.created_at = kwargs.get("created_at", None)
        self.updated_at = kwargs.get("updated_at", None)


class MockMessage:
    """模拟 Message ORM 对象。"""

    def __init__(self, **kwargs):
        self.id = kwargs.get("id", MSG_ID)
        self.conversation_id = kwargs.get("conversation_id", CONV_ID)  # VARCHAR 列
        self.role = kwargs.get("role", "user")
        self.content = kwargs.get("content", "测试消息")
        self.sources = kwargs.get("sources", None)
        self.token_count = kwargs.get("token_count", None)
        self.created_at = kwargs.get("created_at", None)
