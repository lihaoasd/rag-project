"""
会话 Repository — conversations + messages 表 CRUD。
"""
import uuid

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models.conversation import Conversation, Message
from utils.logging import get_logger

logger = get_logger(__name__)


class ConversationRepository:
    """封装 conversations 和 messages 表的常见操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(
        self,
        session_id: str | None,
        kb_id: str,
        title: str | None = None,
    ) -> Conversation:
        """获取已有会话或创建新会话。

        Args:
            session_id: 已有会话 ID（可选）
            kb_id: 知识库 ID
            title: 会话标题（新建时使用，默认用当前时间）
        """
        if session_id:
            try:
                uid = uuid.UUID(session_id)
                stmt = select(Conversation).where(Conversation.id == uid)
                result = await self.session.execute(stmt)
                conv = result.scalar_one_or_none()
                if conv:
                    return conv
            except ValueError:
                pass  # 无效 session_id，创建新会话

        # 创建新会话
        conv = Conversation(
            id=uuid.uuid4(),
            kb_id=uuid.UUID(kb_id),
            title=title,
            message_count=0,
        )
        self.session.add(conv)
        await self.session.commit()
        await self.session.refresh(conv)
        logger.info("创建新会话: conv_id=%s, kb_id=%s", conv.id, kb_id)
        return conv

    async def create_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
        token_count: int | None = None,
    ) -> Message:
        """创建消息并更新会话消息计数。"""
        uid = uuid.UUID(conv_id)
        msg = Message(
            id=uuid.uuid4(),
            conversation_id=uid,
            role=role,
            content=content,
            sources=sources,
            token_count=token_count,
        )
        self.session.add(msg)

        # 原子更新消息计数
        stmt = (
            update(Conversation)
            .where(Conversation.id == uid)
            .values(message_count=Conversation.message_count + 1)
        )
        await self.session.execute(stmt)
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def delete_messages_by_conv_id(self, conv_id: str) -> None:
        """手动删除会话的所有消息（无 FK，需手动级联）。"""
        try:
            uid = uuid.UUID(conv_id)
        except ValueError:
            return
        stmt = delete(Message).where(Message.conversation_id == uid)
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("消息已删除: conv_id=%s, count=%d", conv_id, result.rowcount)

    async def delete_messages_by_kb_id(self, kb_id: str) -> None:
        """手动删除知识库下所有会话的消息。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return
        # 先找出该 KB 下的所有会话
        stmt = select(Conversation.id).where(Conversation.kb_id == uid)
        result = await self.session.execute(stmt)
        conv_ids = [row[0] for row in result.all()]
        if not conv_ids:
            return
        stmt = delete(Message).where(Message.conversation_id.in_(conv_ids))
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("消息已删除: kb_id=%s, count=%d", kb_id, result.rowcount)

    async def delete_conversations_by_kb_id(self, kb_id: str) -> None:
        """手动删除知识库下所有会话。"""
        try:
            uid = uuid.UUID(kb_id)
        except ValueError:
            return
        stmt = delete(Conversation).where(Conversation.kb_id == uid)
        result = await self.session.execute(stmt)
        await self.session.commit()
        logger.debug("会话已删除: kb_id=%s, count=%d", kb_id, result.rowcount)

    async def get_history(
        self,
        conv_id: str,
        limit: int = 10,
    ) -> list[Message]:
        """获取会话最近的 N 条消息，按时间正序。"""
        try:
            uid = uuid.UUID(conv_id)
        except ValueError:
            return []
        stmt = (
            select(Message)
            .where(Message.conversation_id == uid)
            .order_by(Message.created_at.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
