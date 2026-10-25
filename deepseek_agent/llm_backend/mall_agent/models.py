"""SQLAlchemy ORM models for mall agent history."""
from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Text, DateTime, JSON

from .db_session import Base


class Conversation(Base):
    __tablename__ = "mall_conversations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, index=True)
    title = Column(String(200), default="")
    message_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Message(Base):
    __tablename__ = "mall_messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(BigInteger, nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user / assistant
    content = Column(Text, nullable=True)
    msg_type = Column(String(32), default="text")  # text / products / intent
    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class UserHistoryState(Base):
    """Per-user history visibility state without deleting conversations."""

    __tablename__ = "mall_agent_user_history_state"

    user_id = Column(String(64), primary_key=True)
    hidden_before = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)