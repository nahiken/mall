"""History CRUD service — conversations and messages persistence layer."""
import logging
from datetime import datetime
from sqlalchemy import select, func, desc, delete

from .db_session import get_session
from .models import Conversation, Message, UserHistoryState

logger = logging.getLogger("mall_agent.history")

PAGE_SIZE = 20


async def _session():
    """Get an async session as context manager.

    Usage: async with (await _session()) as session:
    """
    return get_session()()


# ── Conversations ───────────────────────────────────────────────────────

async def create_conversation(user_id: str, title: str = "新对话") -> int:
    """Create a new conversation. Returns conversation_id."""
    async with await _session() as session:
        conv = Conversation(user_id=user_id, title=title)
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
        logger.info("Created conversation %d for user %s", conv.id, user_id)
        return conv.id


async def list_conversations(
    user_id: str,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> tuple[list[dict], int]:
    """List visible conversations for a user, paginated by updated_at desc."""
    async with await _session() as session:
        state = await session.get(UserHistoryState, user_id)
        filters = [Conversation.user_id == user_id]
        if state is not None:
            filters.append(Conversation.created_at > state.hidden_before)

        count_q = select(func.count(Conversation.id)).where(*filters)
        total = (await session.execute(count_q)).scalar() or 0

        q = (
            select(Conversation)
            .where(*filters)
            .order_by(desc(Conversation.updated_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await session.execute(q)).scalars().all()

        return [
            {
                "id": c.id,
                "title": c.title,
                "message_count": c.message_count,
                "created_at": c.created_at.isoformat() if c.created_at else "",
                "updated_at": c.updated_at.isoformat() if c.updated_at else "",
            }
            for c in rows
        ], total


async def get_conversation(
    conversation_id: int,
    user_id: str | None = None,
) -> dict | None:
    """Get a conversation, optionally requiring ownership by user_id."""
    async with await _session() as session:
        c = await session.get(Conversation, conversation_id)
        if c is None or (user_id is not None and c.user_id != user_id):
            return None
        return {
            "id": c.id,
            "user_id": c.user_id,
            "title": c.title,
            "message_count": c.message_count,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "updated_at": c.updated_at.isoformat() if c.updated_at else "",
        }


async def hide_history(user_id: str) -> datetime:
    """Hide all existing conversations for a user without deleting them."""
    hidden_before = datetime.now()
    async with await _session() as session:
        state = await session.get(UserHistoryState, user_id)
        if state is None:
            state = UserHistoryState(
                user_id=user_id,
                hidden_before=hidden_before,
                updated_at=hidden_before,
            )
            session.add(state)
        else:
            state.hidden_before = hidden_before
            state.updated_at = hidden_before
        await session.commit()
    logger.info("History hidden for user %s before %s", user_id, hidden_before.isoformat())
    return hidden_before

async def update_conversation_title(conversation_id: int, title: str):
    """Update conversation title."""
    async with await _session() as session:
        c = await session.get(Conversation, conversation_id)
        if c:
            c.title = title
            c.updated_at = datetime.now()
            await session.commit()


async def delete_conversation(conversation_id: int):
    """Delete a conversation and all its messages."""
    async with await _session() as session:
        await session.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        await session.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await session.commit()
        logger.info("Deleted conversation %d", conversation_id)


# ── Messages ────────────────────────────────────────────────────────────

async def save_message(
    conversation_id: int,
    role: str,
    content: str,
    msg_type: str = "text",
    extra: dict | None = None,
) -> int:
    """Save a single message. Returns message_id."""
    async with await _session() as session:
        msg = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            msg_type=msg_type,
            extra=extra,
        )
        session.add(msg)

        conv = await session.get(Conversation, conversation_id)
        if conv:
            conv.message_count = (conv.message_count or 0) + 1
            conv.updated_at = datetime.now()

        await session.commit()
        await session.refresh(msg)
        return msg.id


async def list_messages(
    conversation_id: int,
    page: int = 1,
    page_size: int = PAGE_SIZE,
) -> tuple[list[dict], int]:
    """List messages for a conversation, oldest-first with pagination.

    Returns (messages, total_count).
    """
    async with await _session() as session:
        count_q = (
            select(func.count(Message.id))
            .where(Message.conversation_id == conversation_id)
        )
        total = (await session.execute(count_q)).scalar() or 0

        total_pages = max(1, (total + page_size - 1) // page_size)
        # page=1 = newest; highest page = oldest
        offset = max(0, total - page * page_size)

        q = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .offset(offset)
            .limit(page_size)
        )
        rows = (await session.execute(q)).scalars().all()
        rows = list(reversed(rows))  # oldest-first

        return [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content or "",
                "msg_type": m.msg_type,
                "extra": m.extra,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in rows
        ], total


async def get_message_context(
    conversation_id: int,
    limit: int = 6,
) -> list[dict]:
    """Get the last N messages as context for LLM calls."""
    async with await _session() as session:
        q = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        rows = (await session.execute(q)).scalars().all()
        messages = []
        for m in reversed(rows):
            if m.msg_type == "text":
                messages.append({"role": m.role, "content": m.content or ""})
        return messages
