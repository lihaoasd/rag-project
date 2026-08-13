"""
ORM 模型包 — 导出所有模型，确保 Alembic 能够发现 Base.metadata。
"""
from .base import Base
from .knowledge_base import KnowledgeBase
from .document import Document, DocumentChunk
from .conversation import Conversation, Message
from .model_config import ModelConfig, KBModelBinding
from .prompt_template import PromptTemplate

__all__ = [
    "Base",
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    "Conversation",
    "Message",
    "ModelConfig",
    "KBModelBinding",
    "PromptTemplate",
]
