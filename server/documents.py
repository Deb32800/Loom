"""
Loom Documents — multi-document workspace REST API
=====================================================

Every document has exactly one owner (set at creation) and zero or more
additional members (added via /share) with role 'editor' or 'viewer'.
Access to a document — including opening its WebSocket — always goes through
a `document_members` row; there is no implicit access.
"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import List, Optional

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from server.auth import WsTicketResponse, get_current_user, get_db, get_redis, issue_ws_ticket
from server.database import Database
from server.document_registry import DocumentRegistry
from server.models import DocumentMemberModel, DocumentModel, UserModel
from server.session_manager import SessionManager

logger = logging.getLogger('loom.documents')

documents_router = APIRouter(prefix='/api/documents', tags=['documents'])

ROLES = ('owner', 'editor', 'viewer')
SHAREABLE_ROLES = ('editor', 'viewer')


async def get_registry(request: Request) -> DocumentRegistry:
    return request.app.state.registry


async def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


async def _get_membership(db: Database, document_id: str, user_id: str) -> Optional[DocumentMemberModel]:
    async with db.session_factory() as session:
        result = await session.execute(
            select(DocumentMemberModel).where(DocumentMemberModel.document_id == document_id, DocumentMemberModel.user_id == user_id)
        )
        return result.scalar_one_or_none()


def _require_role(membership: Optional[DocumentMemberModel], allowed: tuple) -> DocumentMemberModel:
    if membership is None or membership.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not have access to this document')
    return membership


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class CreateDocumentRequest(BaseModel):
    title: str = Field(default='Untitled Document', max_length=200)


class RenameDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ShareRequest(BaseModel):
    username: str
    role: str = Field(pattern='^(editor|viewer)$')


class DocumentSummary(BaseModel):
    id: str
    title: str
    role: str
    owner_id: str
    updated_at: Optional[datetime]


class MemberSummary(BaseModel):
    user_id: str
    username: str
    role: str


class DocumentDetail(BaseModel):
    id: str
    title: str
    role: str
    owner_id: str
    members: List[MemberSummary]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@documents_router.post('', response_model=DocumentSummary, status_code=status.HTTP_201_CREATED)
async def create_document(body: CreateDocumentRequest, user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    async with db.session_factory() as session:
        doc = DocumentModel(owner_id=user.id, title=body.title, content='', revision=0)
        session.add(doc)
        await session.flush()  # assigns doc.id via its Python-side default
        session.add(DocumentMemberModel(document_id=doc.id, user_id=user.id, role='owner'))
        await session.commit()
        await session.refresh(doc)
    return DocumentSummary(id=doc.id, title=doc.title, role='owner', owner_id=doc.owner_id, updated_at=doc.updated_at)


@documents_router.get('', response_model=List[DocumentSummary])
async def list_documents(user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    async with db.session_factory() as session:
        result = await session.execute(
            select(DocumentModel, DocumentMemberModel.role)
            .join(DocumentMemberModel, DocumentMemberModel.document_id == DocumentModel.id)
            .where(DocumentMemberModel.user_id == user.id)
            .order_by(DocumentModel.updated_at.desc())
        )
        rows = result.all()
    return [DocumentSummary(id=doc.id, title=doc.title, role=role, owner_id=doc.owner_id, updated_at=doc.updated_at) for doc, role in rows]


@documents_router.get('/{document_id}', response_model=DocumentDetail)
async def get_document(document_id: str, user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    membership = _require_role(await _get_membership(db, document_id, user.id), ROLES)
    async with db.session_factory() as session:
        doc = await session.get(DocumentModel, document_id)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
        result = await session.execute(
            select(DocumentMemberModel, UserModel.username)
            .join(UserModel, UserModel.id == DocumentMemberModel.user_id)
            .where(DocumentMemberModel.document_id == document_id)
        )
        members = [MemberSummary(user_id=m.user_id, username=username, role=m.role) for m, username in result.all()]
    return DocumentDetail(id=doc.id, title=doc.title, role=membership.role, owner_id=doc.owner_id, members=members)


@documents_router.patch('/{document_id}', response_model=DocumentSummary)
async def rename_document(document_id: str, body: RenameDocumentRequest, user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    _require_role(await _get_membership(db, document_id, user.id), ('owner', 'editor'))
    async with db.session_factory() as session:
        doc = await session.get(DocumentModel, document_id)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
        doc.title = body.title
        await session.commit()
        await session.refresh(doc)
    return DocumentSummary(id=doc.id, title=doc.title, role='editor', owner_id=doc.owner_id, updated_at=doc.updated_at)


@documents_router.delete('/{document_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    user: UserModel = Depends(get_current_user),
    db: Database = Depends(get_db),
    registry: DocumentRegistry = Depends(get_registry),
    session_manager: SessionManager = Depends(get_session_manager),
):
    _require_role(await _get_membership(db, document_id, user.id), ('owner',))
    async with db.session_factory() as session:
        doc = await session.get(DocumentModel, document_id)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
        from server.models import OperationModel
        await session.execute(OperationModel.__table__.delete().where(OperationModel.document_id == document_id))
        await session.execute(DocumentMemberModel.__table__.delete().where(DocumentMemberModel.document_id == document_id))
        await session.delete(doc)
        await session.commit()

    # If this document is currently loaded in memory (clients connected),
    # evict it without flushing — the rows are gone, so a flush would just
    # recreate a broken, ownerless row — and tell any connected clients.
    registry.evict(document_id)
    await session_manager.broadcast(document_id, {'type': 'document_deleted'})
    return None


@documents_router.post('/{document_id}/share', response_model=MemberSummary)
async def share_document(document_id: str, body: ShareRequest, user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    _require_role(await _get_membership(db, document_id, user.id), ('owner',))
    async with db.session_factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == body.username))
        target = result.scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No such user')
        if target.id == user.id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='You already own this document')

        result = await session.execute(
            select(DocumentMemberModel).where(DocumentMemberModel.document_id == document_id, DocumentMemberModel.user_id == target.id)
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.role = body.role
        else:
            session.add(DocumentMemberModel(document_id=document_id, user_id=target.id, role=body.role))
        await session.commit()
    return MemberSummary(user_id=target.id, username=target.username, role=body.role)


@documents_router.delete('/{document_id}/share/{user_id}', status_code=status.HTTP_204_NO_CONTENT)
async def unshare_document(document_id: str, user_id: str, user: UserModel = Depends(get_current_user), db: Database = Depends(get_db)):
    _require_role(await _get_membership(db, document_id, user.id), ('owner',))
    async with db.session_factory() as session:
        doc = await session.get(DocumentModel, document_id)
        if doc is not None and doc.owner_id == user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Can't remove the owner")
        await session.execute(
            DocumentMemberModel.__table__.delete().where(DocumentMemberModel.document_id == document_id, DocumentMemberModel.user_id == user_id)
        )
        await session.commit()
    return None


@documents_router.post('/{document_id}/ws-ticket', response_model=WsTicketResponse)
async def document_ws_ticket(
    document_id: str,
    user: UserModel = Depends(get_current_user),
    db: Database = Depends(get_db),
    redis_client: redis.Redis = Depends(get_redis),
):
    membership = _require_role(await _get_membership(db, document_id, user.id), ROLES)
    async with db.session_factory() as session:
        doc = await session.get(DocumentModel, document_id)
        if doc is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Document not found')
        title = doc.title
    ticket = await issue_ws_ticket(redis_client, user.id, user.username, document_id, membership.role, title)
    return WsTicketResponse(ticket=ticket)
