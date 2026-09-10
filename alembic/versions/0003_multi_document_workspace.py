"""multi-document workspace — owner_id/title on documents, document_members

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pre-multi-tenant documents (the old single hardcoded "main" document) have
    # no owner and can't be meaningfully migrated to the new per-user model —
    # clear them so the new NOT NULL owner_id constraint can be added cleanly.
    op.execute('DELETE FROM operations')
    op.execute('DELETE FROM documents')

    op.add_column('documents', sa.Column('owner_id', sa.String(), nullable=False))
    op.add_column('documents', sa.Column('title', sa.String(), nullable=False, server_default='Untitled Document'))
    op.add_column('documents', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key('fk_documents_owner_id_users', 'documents', 'users', ['owner_id'], ['id'])
    op.create_index('ix_documents_owner_id', 'documents', ['owner_id'])

    op.create_table(
        'document_members',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('document_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', 'user_id', name='uq_document_members_doc_user'),
    )
    op.create_index('ix_document_members_document_id', 'document_members', ['document_id'])
    op.create_index('ix_document_members_user_id', 'document_members', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_document_members_user_id', table_name='document_members')
    op.drop_index('ix_document_members_document_id', table_name='document_members')
    op.drop_table('document_members')
    op.drop_index('ix_documents_owner_id', table_name='documents')
    op.drop_constraint('fk_documents_owner_id_users', 'documents', type_='foreignkey')
    op.drop_column('documents', 'created_at')
    op.drop_column('documents', 'title')
    op.drop_column('documents', 'owner_id')
