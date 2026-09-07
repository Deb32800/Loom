
from __future__ import annotations
import os
import json
import logging
from typing import Optional
import aiosqlite
from server.ot_engine import Insert, Delete, NoOp
logger = logging.getLogger('loom.database')
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'loom.db')

class Database:
    """Async SQLite database for persisting Loom documents.
    
    Usage:
        db = Database("loom.db")
        await db.initialize()      # create tables if they don't exist
        
        # Save document state
        await db.save_document("main", "Hello World", revision=5)
        
        # Save an operation
        await db.save_operation("main", Insert(0, "X"), revision=6, client_id="abc")
        
        # Load on startup
        content, revision = await db.load_document("main")
        
        await db.close()
    """

    def __init__(self, db_path: str=DEFAULT_DB_PATH):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Open the database connection and create tables if needed.
        
        This must be called before any other database method.
        """
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute('PRAGMA journal_mode=WAL')
        await self._conn.execute("\n            CREATE TABLE IF NOT EXISTS documents (\n                id          TEXT PRIMARY KEY,\n                content     TEXT NOT NULL DEFAULT '',\n                revision    INTEGER NOT NULL DEFAULT 0,\n                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n            )\n        ")
        await self._conn.execute('\n            CREATE TABLE IF NOT EXISTS operations (\n                id          INTEGER PRIMARY KEY AUTOINCREMENT,\n                document_id TEXT NOT NULL,\n                revision    INTEGER NOT NULL,\n                op_type     TEXT NOT NULL,\n                position    INTEGER NOT NULL,\n                text        TEXT,\n                count       INTEGER,\n                client_id   TEXT NOT NULL,\n                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n                FOREIGN KEY (document_id) REFERENCES documents(id)\n            )\n        ')
        await self._conn.execute('\n            CREATE INDEX IF NOT EXISTS idx_ops_doc_rev\n            ON operations(document_id, revision)\n        ')
        await self._conn.commit()
        logger.info(f'Database initialized: {self.db_path}')

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info('Database connection closed')

    async def save_document(self, document_id: str, content: str, revision: int) -> None:
        """Save (upsert) the current document state.
        
        Uses INSERT OR REPLACE so it works for both new and existing documents.
        Called periodically or after each operation to persist the latest state.
        """
        await self._conn.execute('\n            INSERT OR REPLACE INTO documents (id, content, revision, updated_at)\n            VALUES (?, ?, ?, CURRENT_TIMESTAMP)\n            ', (document_id, content, revision))
        await self._conn.commit()

    async def load_document(self, document_id: str) -> Optional[tuple]:
        """Load a document's content and revision.
        
        Returns:
            Tuple of (content, revision) if found, None if not found.
        """
        async with self._conn.execute('SELECT content, revision FROM documents WHERE id = ?', (document_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return (row[0], row[1])
            return None

    async def save_operation(self, document_id: str, op, revision: int, client_id: str) -> None:
        """Save a single operation to the history.
        
        This is called after each operation is applied to the document.
        """
        if isinstance(op, Insert):
            await self._conn.execute("\n                INSERT INTO operations\n                    (document_id, revision, op_type, position, text, client_id)\n                VALUES (?, ?, 'insert', ?, ?, ?)\n                ", (document_id, revision, op.position, op.text, client_id))
        elif isinstance(op, Delete):
            await self._conn.execute("\n                INSERT INTO operations\n                    (document_id, revision, op_type, position, count, client_id)\n                VALUES (?, ?, 'delete', ?, ?, ?)\n                ", (document_id, revision, op.position, op.count, client_id))
        await self._conn.commit()

    async def load_operations(self, document_id: str, from_revision: int=0) -> list:
        """Load operations from the history, starting at a given revision.
        
        Args:
            document_id:    Which document's history to load.
            from_revision:  Load operations starting from this revision.
                           0 means load everything.
        
        Returns:
            List of dicts with op details, ordered by revision.
        """
        rows = []
        async with self._conn.execute('\n            SELECT revision, op_type, position, text, count, client_id\n            FROM operations\n            WHERE document_id = ? AND revision >= ?\n            ORDER BY revision ASC\n            ', (document_id, from_revision)) as cursor:
            async for row in cursor:
                (revision, op_type, position, text, count, client_id) = row
                if op_type == 'insert':
                    op = Insert(position=position, text=text)
                elif op_type == 'delete':
                    op = Delete(position=position, count=count)
                else:
                    continue
                rows.append({'op': op, 'revision': revision, 'client_id': client_id})
        return rows