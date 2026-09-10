from __future__ import annotations
import os
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from server.ot_engine import Insert, Delete
from server.models import Base, DocumentModel, OperationModel
logger = logging.getLogger('loom.database')

DEFAULT_DATABASE_URL = 'postgresql+asyncpg://loom:loom@localhost:5432/loom'

class Database:
    """Async PostgreSQL persistence for Loom documents, via SQLAlchemy 2.0's async ORM.

    Usage:
        db = Database()                # reads DATABASE_URL, falls back to DEFAULT_DATABASE_URL
        await db.initialize()

        await db.save_document("main", "Hello World", revision=5)
        await db.save_operation("main", Insert(0, "X"), revision=6, client_id="abc")
        content, revision = await db.load_document("main")

        await db.close()

    Schema is owned by Alembic (see alembic/versions/) for real deployments and
    upgrades. `initialize()` also runs a `create_all`, so a bare clone works
    without running migrations first (dev/test convenience) — Alembic and this
    create_all describe the same schema in server/models.py, so they can't drift.
    """

    def __init__(self, database_url: Optional[str]=None):
        self.database_url = database_url or os.environ.get('DATABASE_URL', DEFAULT_DATABASE_URL)
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker] = None

    async def initialize(self) -> None:
        """Open the database engine and create tables if needed.

        This must be called before any other database method.
        """
        self._engine = create_async_engine(self.database_url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f'Database initialized: {self.database_url}')

    async def close(self) -> None:
        """Close the database engine."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            logger.info('Database connection closed')

    async def save_document(self, document_id: str, content: str, revision: int) -> None:
        """Save (upsert) the current document state.

        Called periodically or after each operation to persist the latest state.
        """
        async with self._session_factory() as session:
            doc = await session.get(DocumentModel, document_id)
            if doc is None:
                session.add(DocumentModel(id=document_id, content=content, revision=revision))
            else:
                doc.content = content
                doc.revision = revision
            await session.commit()

    async def load_document(self, document_id: str) -> Optional[tuple]:
        """Load a document's content and revision.

        Returns:
            Tuple of (content, revision) if found, None if not found.
        """
        async with self._session_factory() as session:
            doc = await session.get(DocumentModel, document_id)
            if doc is None:
                return None
            return (doc.content, doc.revision)

    async def save_operation(self, document_id: str, op, revision: int, client_id: str) -> None:
        """Save a single operation to the history.

        This is called after each operation is applied to the document.
        """
        if isinstance(op, Insert):
            row = OperationModel(document_id=document_id, revision=revision, op_type='insert', position=op.position, text=op.text, client_id=client_id)
        elif isinstance(op, Delete):
            row = OperationModel(document_id=document_id, revision=revision, op_type='delete', position=op.position, count=op.count, client_id=client_id)
        else:
            return
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

    async def load_operations(self, document_id: str, from_revision: int=0) -> list:
        """Load operations from the history, starting at a given revision.

        Args:
            document_id:    Which document's history to load.
            from_revision:  Load operations starting from this revision.
                           0 means load everything.

        Returns:
            List of dicts with op details, ordered by revision.
        """
        async with self._session_factory() as session:
            stmt = select(OperationModel).where(OperationModel.document_id == document_id, OperationModel.revision >= from_revision).order_by(OperationModel.revision.asc())
            result = await session.execute(stmt)
            rows = []
            for row in result.scalars():
                if row.op_type == 'insert':
                    op = Insert(position=row.position, text=row.text)
                elif row.op_type == 'delete':
                    op = Delete(position=row.position, count=row.count)
                else:
                    continue
                rows.append({'op': op, 'revision': row.revision, 'client_id': row.client_id})
            return rows
