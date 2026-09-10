"""
Loom Auth — JWT-based authentication
=====================================

- Signup/login with argon2id password hashing.
- Short-lived JWT access tokens (Authorization: Bearer ...) for REST calls.
- Long-lived, revocable, rotated-on-use refresh tokens: opaque random strings,
  stored server-side only as a SHA-256 hash, delivered to the browser as an
  httpOnly cookie scoped to /api/auth.
- Single-use, short-TTL WebSocket "tickets": browsers can't set custom headers
  on a WS handshake, and a long-lived JWT in the query string would end up in
  server access logs, so a client fetches a one-time ticket over authenticated
  REST immediately before opening the socket. Tickets live in an in-memory
  dict for now (single process) — a later phase moves this store to Redis so
  it keeps working once there's more than one app instance.
"""
from __future__ import annotations
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select

from server.database import Database
from server.models import RefreshTokenModel, UserModel

logger = logging.getLogger('loom.auth')

JWT_SECRET = os.environ.get('JWT_SECRET', 'dev-secret-do-not-use-in-production')
JWT_ALGORITHM = 'HS256'
ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
WS_TICKET_TTL = timedelta(seconds=30)
REFRESH_COOKIE_NAME = 'loom_refresh_token'

_hasher = PasswordHasher()
_bearer_scheme = HTTPBearer(auto_error=False)

if JWT_SECRET == 'dev-secret-do-not-use-in-production':
    logger.warning('JWT_SECRET is not set — using an insecure development default. Set JWT_SECRET in production.')


# ---------------------------------------------------------------------------
# Password hashing (argon2id)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: str, username: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {'sub': user_id, 'username': username, 'type': 'access', 'iat': now, 'exp': now + ACCESS_TOKEN_TTL}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get('type') != 'access':
        raise jwt.InvalidTokenError('not an access token')
    return payload


# ---------------------------------------------------------------------------
# Refresh tokens — opaque, stored hashed, single-use (rotated on every refresh)
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


async def _issue_refresh_token(db: Database, user_id: str) -> str:
    raw = secrets.token_urlsafe(32)
    async with db.session_factory() as session:
        session.add(RefreshTokenModel(user_id=user_id, token_hash=_hash_token(raw), expires_at=datetime.now(timezone.utc) + REFRESH_TOKEN_TTL))
        await session.commit()
    return raw


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=int(REFRESH_TOKEN_TTL.total_seconds()),
        httponly=True,
        samesite='lax',
        secure=False,  # TODO: set True once served over HTTPS
        path='/api/auth',
    )


# ---------------------------------------------------------------------------
# WebSocket tickets
# ---------------------------------------------------------------------------

class _TicketInfo:
    __slots__ = ('user_id', 'username', 'document_id', 'role', 'title', 'expires_at')

    def __init__(self, user_id: str, username: str, document_id: str, role: str, title: str, expires_at: datetime):
        self.user_id = user_id
        self.username = username
        self.document_id = document_id
        self.role = role
        self.title = title
        self.expires_at = expires_at


_ws_tickets: dict = {}


def issue_ws_ticket(user_id: str, username: str, document_id: str, role: str, title: str) -> str:
    """Issue a single-use ticket bound to one user, one document, and the
    role they're allowed to act with on it. The WS endpoint derives its
    document_id and permissions from this ticket rather than trusting
    anything the client sends directly."""
    ticket = secrets.token_urlsafe(24)
    _ws_tickets[ticket] = _TicketInfo(user_id, username, document_id, role, title, datetime.now(timezone.utc) + WS_TICKET_TTL)
    return ticket


def consume_ws_ticket(ticket: str) -> Optional[_TicketInfo]:
    info = _ws_tickets.pop(ticket, None)
    if info is None:
        return None
    if info.expires_at < datetime.now(timezone.utc):
        return None
    return info


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

async def get_db(request: Request) -> Database:
    return request.app.state.db


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    db: Database = Depends(get_db),
) -> UserModel:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired access token')
    async with db.session_factory() as session:
        user = await session.get(UserModel, payload['sub'])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User no longer exists')
    return user


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    username: str
    password: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    username: str


class UserResponse(BaseModel):
    user_id: str
    username: str


class WsTicketResponse(BaseModel):
    ticket: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

auth_router = APIRouter(prefix='/api/auth', tags=['auth'])


@auth_router.post('/signup', response_model=AccessTokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, response: Response, db: Database = Depends(get_db)):
    async with db.session_factory() as session:
        existing = await session.execute(select(UserModel).where(UserModel.username == body.username))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Username already taken')
        user = UserModel(username=body.username, password_hash=hash_password(body.password))
        session.add(user)
        await session.commit()
        await session.refresh(user)

    access_token = create_access_token(user.id, user.username)
    refresh_token = await _issue_refresh_token(db, user.id)
    _set_refresh_cookie(response, refresh_token)
    logger.info(f'New user signed up: {user.username}')
    return AccessTokenResponse(access_token=access_token, username=user.username)


@auth_router.post('/login', response_model=AccessTokenResponse)
async def login(body: LoginRequest, response: Response, db: Database = Depends(get_db)):
    async with db.session_factory() as session:
        result = await session.execute(select(UserModel).where(UserModel.username == body.username))
        user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid username or password')

    access_token = create_access_token(user.id, user.username)
    refresh_token = await _issue_refresh_token(db, user.id)
    _set_refresh_cookie(response, refresh_token)
    return AccessTokenResponse(access_token=access_token, username=user.username)


@auth_router.post('/refresh', response_model=AccessTokenResponse)
async def refresh(
    response: Response,
    db: Database = Depends(get_db),
    loom_refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    if not loom_refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing refresh token')

    token_hash = _hash_token(loom_refresh_token)
    async with db.session_factory() as session:
        result = await session.execute(select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash))
        row = result.scalar_one_or_none()
        if row is None or row.revoked or row.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token invalid or expired')
        row.revoked = True  # single-use: rotate on every refresh
        user = await session.get(UserModel, row.user_id)
        await session.commit()

    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User no longer exists')

    access_token = create_access_token(user.id, user.username)
    new_refresh_token = await _issue_refresh_token(db, user.id)
    _set_refresh_cookie(response, new_refresh_token)
    return AccessTokenResponse(access_token=access_token, username=user.username)


@auth_router.post('/logout', status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: Database = Depends(get_db),
    loom_refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    if loom_refresh_token:
        token_hash = _hash_token(loom_refresh_token)
        async with db.session_factory() as session:
            result = await session.execute(select(RefreshTokenModel).where(RefreshTokenModel.token_hash == token_hash))
            row = result.scalar_one_or_none()
            if row is not None:
                row.revoked = True
                await session.commit()
    response.delete_cookie(REFRESH_COOKIE_NAME, path='/api/auth')
    return None


@auth_router.get('/me', response_model=UserResponse)
async def me(user: UserModel = Depends(get_current_user)):
    return UserResponse(user_id=user.id, username=user.username)


# WebSocket tickets are now issued per-document (a connection is only ever
# for one document, and the ticket needs to carry that document's id and the
# caller's role on it) — see POST /api/documents/{document_id}/ws-ticket in
# server/documents.py, which checks membership before calling issue_ws_ticket.
