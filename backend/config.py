"""
应用配置管理 — 基于 pydantic-settings 从环境变量读取所有配置项。
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """投标文档 RAG 系统配置"""

    # === LLM 配置 ===
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_api_key: str = ""
    llm_api_base: str = "https://api.deepseek.com/v1"
    llm_temperature: float = 0.1

    # === Embedding 配置 (Xinference) ===
    embedding_api_base: str = "http://localhost:9997/v1"
    embedding_api_key: str = ""
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # === Milvus 配置 ===
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    @property
    def milvus_uri(self) -> str:
        """构建 Milvus 连接 URI（MilvusClient 格式）。"""
        return f"http://{self.milvus_host}:{self.milvus_port}"

    # === PostgreSQL 配置 ===
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "ai_bid_rag"
    pg_user: str = "postgres"
    pg_password: str = "postgres"

    # === MinIO 配置 ===
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "bid-documents"
    minio_secure: bool = False

    # === 文档处理配置 ===
    chunk_size: int = 512
    chunk_overlap: int = 64
    # Word 逻辑块（标题聚合）超长二次切分参数（字符数）
    word_block_max_chars: int = 1000
    word_block_overlap_chars: int = 100
    parse_batch_size: int = 16
    retrieval_top_k: int = 5
    retrieval_similarity_threshold: float = 0.6
    retrieval_rerank_threshold: float = 0.8
    retrieval_hybrid_enabled: bool = True  # Milvus 稀疏字段开关（BM25 稀疏策略依赖；关闭时 sparse 不可用）

    # === Elasticsearch 全文检索配置 ===
    es_host: str = "localhost"
    es_port: int = 9200
    es_index_name: str = "bid_chunks"
    es_sync_enabled: bool = True          # ES 双写开关
    es_username: str = ""
    es_password: str = ""
    es_highlight_enabled: bool = True     # 检索是否返回高亮片段

    # === 检索方式 ===
    retrieval_default_methods: list[str] = ["dense"]  # 未传 retrieval_methods 时默认勾选的策略（dense/sparse/fulltext）
    chat_retrieval_methods: list[str] = ["dense", "sparse", "fulltext"]  # 问答（chat）固定三路混合检索策略
    retrieval_es_top_k_factor: int = 2          # fulltext 召回放大系数
    retrieval_rrf_k: int = 60                   # RRF 常数

    # === Query 改写 ===
    query_rewrite_enabled: bool = False          # 检索页服务端默认开关（请求未显式传 query_rewrite 时生效；默认关）
    chat_query_rewrite_enabled: bool = True     # 问答（chat）Query 改写开关（聊天页无前端控件，后端配置；默认关）

    # === ES 对账 ===
    es_reconcile_enabled: bool = True           # 每日定时对账开关
    es_reconcile_interval_hours: int = 24       # 对账间隔（小时）

    # === 服务配置 ===
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    temp_dir: str = "./temp"
    max_upload_size: int = 524288000  # 500MB

    # === 日志配置 ===
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # === Langfuse 观测配置 ===
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""
    langfuse_sample_rate: float = 1.0

    # === Neo4j 图谱配置 ===
    graph_enabled: bool = False          # 图谱功能总开关（未部署 Neo4j 时优雅降级）
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "password123"
    neo4j_database: str = "neo4j"
    graph_extraction_batch: int = 1      # LLM 抽取：逐 chunk 提取（每 chunk 一次调用，减少跨 chunk 幻觉；可调大合并节省成本）
    graph_max_hops: int = 2              # 图检索最大跳数
    graph_query_limit: int = 200         # 子图返回上限
    graph_min_chunk_chars: int = 20      # 短于此长度的 chunk 不参与抽取

    @property
    def database_url(self) -> str:
        """构建异步 PostgreSQL 连接字符串"""
        return (
            f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    @property
    def database_url_sync(self) -> str:
        """构建同步 PostgreSQL 连接字符串（Alembic 离线模式使用）"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# 全局单例
settings = Settings()
