"""init: 7 tables — knowledge_bases, documents, document_chunks, conversations, messages, llm_configs, embedding_configs

Revision ID: 0001
Revises:
Create Date: 2026-08-07
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 扩展：UUID 生成（PG 9.6 兼容） ===
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # === 通用函数：自动更新 updated_at ===
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)

    # ============================================
    # 1. knowledge_bases
    # ============================================
    op.create_table(
        "knowledge_bases",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("document_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_knowledge_bases_name"),
    )
    op.create_index("idx_knowledge_bases_created_at", "knowledge_bases", ["created_at"], unique=False,
                    postgresql_ops={"created_at": "DESC"})
    op.execute("""
        CREATE TRIGGER trg_knowledge_bases_updated_at
            BEFORE UPDATE ON knowledge_bases
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 2. documents
    # ============================================
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(512), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.String(10), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default="uploading", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_documents_object_key"),
    )
    op.execute("""
        ALTER TABLE documents ADD CONSTRAINT chk_documents_file_type
            CHECK (file_type IN ('pdf', 'docx', 'doc'));
    """)
    op.execute("""
        ALTER TABLE documents ADD CONSTRAINT chk_documents_status
            CHECK (status IN ('uploading', 'processing', 'completed', 'failed'));
    """)
    op.create_index("idx_documents_kb_id", "documents", ["kb_id"], unique=False)
    op.create_index("idx_documents_kb_status", "documents", ["kb_id", "status"], unique=False)
    op.create_index("idx_documents_created_at", "documents", ["created_at"], unique=False,
                    postgresql_ops={"created_at": "DESC"})
    op.execute("""
        CREATE TRIGGER trg_documents_updated_at
            BEFORE UPDATE ON documents
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 3. document_chunks
    # ============================================
    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("doc_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("milvus_pk", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["doc_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_id", "chunk_index", name="uq_chunks_doc_chunk"),
    )
    op.create_index("idx_chunks_doc_id", "document_chunks", ["doc_id"], unique=False)
    op.create_index("idx_chunks_kb_id", "document_chunks", ["kb_id"], unique=False)

    # ============================================
    # 4. conversations
    # ============================================
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["kb_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_conversations_kb_id", "conversations", ["kb_id"], unique=False)
    op.create_index("idx_conversations_updated_at", "conversations", ["updated_at"], unique=False,
                    postgresql_ops={"updated_at": "DESC"})
    op.execute("""
        CREATE TRIGGER trg_conversations_updated_at
            BEFORE UPDATE ON conversations
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 5. messages
    # ============================================
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("""
        ALTER TABLE messages ADD CONSTRAINT chk_messages_role
            CHECK (role IN ('user', 'assistant', 'system'));
    """)
    op.create_index("idx_messages_conversation_id", "messages", ["conversation_id"], unique=False)
    op.create_index("idx_messages_created_at", "messages", ["conversation_id", "created_at"], unique=False)

    # ============================================
    # 6. llm_configs
    # ============================================
    op.create_table(
        "llm_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("api_key", sa.String(512), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=True),
        sa.Column("temperature", sa.Double(), server_default="0.1", nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("top_k", sa.Integer(), server_default="5", nullable=False),
        sa.Column("similarity_threshold", sa.Double(), server_default="0.6", nullable=False),
        sa.Column("context_window", sa.Integer(), server_default="4096", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_configs_name"),
    )
    op.execute("""
        ALTER TABLE llm_configs ADD CONSTRAINT chk_llm_configs_provider
            CHECK (provider IN ('deepseek', 'openai', 'openai_compatible'));
    """)
    op.execute("""
        ALTER TABLE llm_configs ADD CONSTRAINT chk_llm_configs_temperature
            CHECK (temperature >= 0 AND temperature <= 2);
    """)
    op.create_index("idx_llm_configs_active", "llm_configs", ["is_active"], unique=False,
                    postgresql_where=sa.text("is_active = TRUE"))
    op.execute("""
        CREATE TRIGGER trg_llm_configs_updated_at
            BEFORE UPDATE ON llm_configs
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # ============================================
    # 7. embedding_configs
    # ============================================
    op.create_table(
        "embedding_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("uuid_generate_v4()"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("api_base", sa.String(512), nullable=False),
        sa.Column("api_key", sa.String(512), nullable=True),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), server_default="512", nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), server_default="64", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_embedding_configs_name"),
    )
    op.execute("""
        ALTER TABLE embedding_configs ADD CONSTRAINT chk_embedding_dimension
            CHECK (dimension > 0);
    """)
    op.execute("""
        ALTER TABLE embedding_configs ADD CONSTRAINT chk_embedding_chunk_size
            CHECK (chunk_size > 0);
    """)
    op.execute("""
        ALTER TABLE embedding_configs ADD CONSTRAINT chk_embedding_chunk_overlap
            CHECK (chunk_overlap >= 0 AND chunk_overlap < chunk_size);
    """)
    op.create_index("idx_embedding_configs_active", "embedding_configs", ["is_active"], unique=False,
                    postgresql_where=sa.text("is_active = TRUE"))
    op.execute("""
        CREATE TRIGGER trg_embedding_configs_updated_at
            BEFORE UPDATE ON embedding_configs
            FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();
    """)

    # === 默认数据 ===
    op.execute("""
        INSERT INTO llm_configs (name, provider, model, api_key, api_base, is_active)
        VALUES (
            'DeepSeek V3 (默认)',
            'deepseek',
            'deepseek-chat',
            '${DEEPSEEK_API_KEY}',
            'https://api.deepseek.com/v1',
            TRUE
        );
    """)
    op.execute("""
        INSERT INTO embedding_configs (name, model, api_base, dimension, is_active)
        VALUES (
            'bge-large-zh-v1.5 (默认)',
            'bge-large-zh-v1.5',
            'http://localhost:9997/v1',
            1024,
            TRUE
        );
    """)


def downgrade() -> None:
    # 按依赖反序删除
    op.drop_table("embedding_configs")
    op.drop_table("llm_configs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("document_chunks")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE;")
