"""
ORM 模型包 — 导出所有模型，确保 Alembic 能够发现 Base.metadata。
"""
from .base import Base
from .knowledge_base import KnowledgeBase
from .domain import DomainProfile
from .document import Document, DocumentChunk
from .conversation import Conversation, Message
from .model_config import ModelConfig, ModelProvider, KBModelBinding
from .prompt_template import PromptTemplate
from .lexicon import LexiconTerm
from .lexicon_mapping import LexiconDomainMapping

__all__ = [
    "Base",
    "KnowledgeBase",
    "DomainProfile",
    "Document",
    "DocumentChunk",
    "Conversation",
    "Message",
    "ModelConfig",
    "ModelProvider",
    "KBModelBinding",
    "PromptTemplate",
    "LexiconTerm",
    "LexiconDomainMapping",
]
