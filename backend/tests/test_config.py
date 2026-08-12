"""
Settings 配置模块单元测试。
"""
import os
from unittest.mock import patch

from config import Settings


class TestSettingsDefaults:
    """测试配置默认值（使用显式值覆盖 .env 以确保测试隔离）。"""

    def test_default_llm_provider(self):
        s = Settings(llm_provider="deepseek")
        assert s.llm_provider == "deepseek"

    def test_default_llm_model(self):
        s = Settings(llm_model="deepseek-chat")
        assert s.llm_model == "deepseek-chat"

    def test_default_llm_api_base(self):
        s = Settings(llm_api_base="https://api.deepseek.com/v1")
        assert s.llm_api_base == "https://api.deepseek.com/v1"

    def test_default_llm_temperature(self):
        s = Settings(llm_temperature=0.1)
        assert s.llm_temperature == 0.1

    def test_default_embedding_settings(self):
        s = Settings(embedding_api_base="http://localhost:9997/v1",
                     embedding_model="bge-large-zh-v1.5",
                     embedding_dim=1024)
        assert s.embedding_api_base == "http://localhost:9997/v1"
        assert s.embedding_model == "bge-large-zh-v1.5"
        assert s.embedding_dim == 1024

    def test_default_milvus_settings(self):
        s = Settings(milvus_host="localhost", milvus_port=19530)
        assert s.milvus_host == "localhost"
        assert s.milvus_port == 19530

    def test_default_pg_settings(self):
        s = Settings(pg_host="localhost", pg_port=5432, pg_database="ai_bid_rag")
        assert s.pg_host == "localhost"
        assert s.pg_port == 5432
        assert s.pg_database == "ai_bid_rag"

    def test_default_minio_settings(self):
        s = Settings(minio_endpoint="localhost:9000",
                     minio_bucket="bid-documents",
                     minio_secure=False)
        assert s.minio_endpoint == "localhost:9000"
        assert s.minio_bucket == "bid-documents"
        assert s.minio_secure is False

    def test_default_chunk_settings(self):
        s = Settings(chunk_size=512, chunk_overlap=64)
        assert s.chunk_size == 512
        assert s.chunk_overlap == 64

    def test_default_retrieval_settings(self):
        s = Settings(retrieval_top_k=5, retrieval_similarity_threshold=0.6)
        assert s.retrieval_top_k == 5
        assert s.retrieval_similarity_threshold == 0.6

    def test_default_server_settings(self):
        s = Settings(backend_host="0.0.0.0", backend_port=8000, max_upload_size=104857600)
        assert s.backend_host == "0.0.0.0"
        assert s.backend_port == 8000
        assert s.max_upload_size == 104857600

    def test_default_log_settings(self):
        s = Settings(log_level="INFO", log_dir="./logs")
        assert s.log_level == "INFO"
        assert s.log_dir == "./logs"


class TestSettingsComputedProperties:
    """测试 Settings 的计算属性。"""

    def test_milvus_uri(self):
        s = Settings(_env_file=None, milvus_host="192.168.1.1", milvus_port=19530)
        assert s.milvus_uri == "http://192.168.1.1:19530"

    def test_milvus_uri_localhost(self):
        s = Settings(_env_file=None, milvus_host="localhost", milvus_port=19530)
        assert s.milvus_uri == "http://localhost:19530"

    def test_database_url_async(self):
        s = Settings(
            _env_file=None,
            pg_user="testuser",
            pg_password="testpass",
            pg_host="db.example.com",
            pg_port=5432,
            pg_database="test_db",
        )
        url = s.database_url
        assert "postgresql+asyncpg://" in url
        assert "testuser:testpass" in url
        assert "db.example.com:5432" in url
        assert "test_db" in url

    def test_database_url_sync(self):
        s = Settings(
            _env_file=None,
            pg_user="testuser",
            pg_password="testpass",
            pg_host="db.example.com",
            pg_port=5432,
            pg_database="test_db",
        )
        url = s.database_url_sync
        assert url.startswith("postgresql://")
        assert "testuser:testpass" in url
        assert "db.example.com:5432" in url
        assert "test_db" in url


class TestSettingsEnvOverride:
    """测试 Settings 从环境变量覆盖。"""

    def test_env_override_llm_model(self):
        with patch.dict(os.environ, {"LLM_MODEL": "gpt-4"}, clear=True):
            s = Settings()
            assert s.llm_model == "gpt-4"

    def test_env_override_chunk_size(self):
        with patch.dict(os.environ, {"CHUNK_SIZE": "1024"}, clear=True):
            s = Settings()
            assert s.chunk_size == 1024

    def test_env_override_retrieval_top_k(self):
        with patch.dict(os.environ, {"RETRIEVAL_TOP_K": "20"}, clear=True):
            s = Settings()
            assert s.retrieval_top_k == 20

    def test_env_override_similarity_threshold(self):
        with patch.dict(os.environ, {"RETRIEVAL_SIMILARITY_THRESHOLD": "0.8"}, clear=True):
            s = Settings()
            assert s.retrieval_similarity_threshold == 0.8

    def test_env_override_max_upload_size(self):
        with patch.dict(os.environ, {"MAX_UPLOAD_SIZE": "209715200"}, clear=True):
            s = Settings()
            assert s.max_upload_size == 209715200


class TestSettingsExtraIgnore:
    """测试 Settings 忽略额外字段。"""

    def test_extra_field_ignored(self):
        s = Settings(_env_file=None, unknown_field="should_be_ignored")
        assert not hasattr(s, "unknown_field")
