"""
SQLAlchemy ORM models for Loom's PostgreSQL schema.

Schema evolution is owned by Alembic (see alembic/versions/). This module is
the single source of truth for table shape; Alembic migrations should mirror
it, not the other way around.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DocumentModel(Base):
    __tablename__ = 'documents'

    id: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default='')
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class OperationModel(Base):
    __tablename__ = 'operations'
    __table_args__ = (Index('idx_ops_doc_rev', 'document_id', 'revision'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String, ForeignKey('documents.id'), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    op_type: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    client_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
