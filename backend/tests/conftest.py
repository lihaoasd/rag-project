"""
测试共享 Fixtures 和 Mock 工具。

所有外部依赖（PostgreSQL、Milvus、MinIO、LLM API）均通过 mock 隔离。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保 backend 目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 从 helpers 导入共享常量和 Mock 类
from tests.helpers import (
    KB_ID,
    DOC_ID,
    MockKB,
    MockDocument,
    MockChunk,
    MockConversation,
    MockMessage,
)


# ============================================================
# Mock AsyncSession
# ============================================================
@pytest.fixture
def mock_session():
    """创建模拟的 AsyncSession。"""
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()
    return session


# ============================================================
# Mock 数据库会话工厂（autouse — patch 消费者模块）
# ============================================================
@pytest.fixture(autouse=True)
def mock_async_session(mock_session):
    """Mock 各 service 层的 async_session，避免连接真实 PG。

    必须在消费者模块上 patch，因为 `from core.database import async_session`
    在导入时创建本地引用，不会随源头替换。
    """
    targets = [
        "services.kb_service.async_session",
        "services.document_service.async_session",
        "services.chat_service.async_session",
        "services.search_service.async_session",
        "services.model_service.async_session",
        "core.model_factory.async_session",
    ]
    active_patches = []
    for target in targets:
        p = patch(target)
        mock_factory = p.start()
        mock_factory.return_value.__aenter__.return_value = mock_session
        mock_factory.return_value.__aexit__.return_value = None
        active_patches.append(p)

    yield

    for p in active_patches:
        p.stop()


# ============================================================
# Mock MinIO 客户端
# ============================================================
@pytest.fixture(autouse=True)
def mock_minio():
    """全局 mock MinIO 客户端操作。"""
    with patch("utils.minio_client.upload") as mock_upload, \
         patch("utils.minio_client.download") as mock_download, \
         patch("utils.minio_client.delete") as mock_delete, \
         patch("utils.minio_client.delete_prefix") as mock_delete_prefix:
        yield {
            "upload": mock_upload,
            "download": mock_download,
            "delete": mock_delete,
            "delete_prefix": mock_delete_prefix,
        }


# ============================================================
# Mock Milvus / Indexing
# ============================================================
@pytest.fixture(autouse=True)
def mock_indexing():
    """全局 mock Milvus 索引操作。"""
    with patch("core.indexing.build_index", new_callable=AsyncMock) as mock_build, \
         patch("core.indexing.delete_index", new_callable=AsyncMock) as mock_delete_idx, \
         patch("core.indexing.drop_kb_collection") as mock_drop, \
         patch("core.indexing.delete_single_vector", new_callable=AsyncMock) as mock_del_single, \
         patch("core.indexing.insert_single_vector", new_callable=AsyncMock) as mock_insert_single:
        mock_build.return_value = [f"pk_{i}" for i in range(10)]
        mock_insert_single.return_value = "pk_new_single"
        yield {
            "build_index": mock_build,
            "delete_index": mock_delete_idx,
            "drop_kb_collection": mock_drop,
            "delete_single_vector": mock_del_single,
            "insert_single_vector": mock_insert_single,
        }


# ============================================================
# Mock Retrieval — patch 源 + 消费者，避免连接真实 Milvus
# ============================================================
_MOCK_RETRIEVE_INSTANCE = None  # 共享的 mock 实例


@pytest.fixture
def mock_retrieve():
    """Mock retrieve 函数，所有 patched target 共享同一个 mock 实例。"""
    global _MOCK_RETRIEVE_INSTANCE
    _MOCK_RETRIEVE_INSTANCE = AsyncMock()
    _MOCK_RETRIEVE_INSTANCE.return_value = [
        {
            "score": 0.95,
            "text": "测试检索文本",
            "doc_id": DOC_ID,
            "file_name": "test.pdf",
            "page_number": 1,
            "chunk_index": 0,
        }
    ]

    patches_data = []
    for target in ["core.retrieval.retrieve",
                   "services.search_service.retrieve",
                   "services.chat_service.retrieve_and_rank"]:
        p = patch(target, new=_MOCK_RETRIEVE_INSTANCE)
        p.start()
        patches_data.append(p)

    yield _MOCK_RETRIEVE_INSTANCE

    for p in patches_data:
        p.stop()
    _MOCK_RETRIEVE_INSTANCE = None


# ============================================================
# Mock LLM — patch 源 + 消费者，避免调用真实 LLM API
# ============================================================
@pytest.fixture
def mock_llm():
    """Mock create_llm，所有 patched target 共享同一个 mock 实例。"""
    llm = MagicMock()
    llm.achat = AsyncMock()
    response = MagicMock()
    response.message = MagicMock()
    response.message.content = "这是 AI 助手的测试回复。"
    llm.achat.return_value = response

    patches_data = []
    for target in ["core.model_factory.create_llm",
                   "services.chat_service.create_llm"]:
        p = patch(target)
        mock_create = p.start()
        mock_create.return_value = llm
        patches_data.append(p)

    yield mock_create

    for p in patches_data:
        p.stop()


# ============================================================
# Mock Embedding
# ============================================================
@pytest.fixture(autouse=True)
def mock_embedding():
    """全局 mock Embedding 创建（避免连接外部 API）。"""
    with patch("core.model_factory.create_embedding") as mock_emb:
        emb = MagicMock()
        emb.aget_text_embedding_batch = AsyncMock(return_value=[[0.1] * 1024])
        emb.aget_text_embedding = AsyncMock(return_value=[0.1] * 1024)
        emb.aget_query_embedding = AsyncMock(return_value=[0.1] * 1024)
        mock_emb.return_value = emb
        yield mock_emb


# ============================================================
# FastAPI TestClient
# ============================================================
@pytest.fixture
def client():
    """创建 FastAPI TestClient（同步）。"""
    # 需要在导入 app 之前 mock 掉耗时的初始化
    with patch("core.parse_queue.start_worker", new_callable=AsyncMock), \
         patch("core.parse_queue.stop_worker", new_callable=AsyncMock):
        from main import app
        from fastapi.testclient import TestClient
        # 使用自定义 lifespan 避免真实的 start_worker
        app.router.lifespan_context = None
        return TestClient(app)


# ============================================================
# Mock UploadFile
# ============================================================
@pytest.fixture
def mock_upload_file():
    """创建模拟的 FastAPI UploadFile。"""
    from io import BytesIO

    content = b"%PDF-1.4 test pdf content"
    file = BytesIO(content)

    upload_file = MagicMock()
    upload_file.filename = "test.pdf"
    upload_file.file = file
    upload_file.size = len(content)
    upload_file.read = AsyncMock(return_value=content)
    return upload_file
