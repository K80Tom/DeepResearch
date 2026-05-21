import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from typing import Any

import psycopg
from fastapi import Depends, Header, HTTPException, status
from psycopg.rows import dict_row

from app.backend.config import AppSettings
from app.backend.schemas import AuthResponse, AuthUser, RegisterRequest


class AuthService:
    def __init__(self, postgres_dsn: str, token_ttl_hours: int = 168):
        self._postgres_dsn = postgres_dsn
        self._token_ttl = timedelta(hours=token_ttl_hours)
        self._initialized = False
        self._lock = Lock()

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            with psycopg.connect(self._postgres_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS app_users (
                            id TEXT PRIMARY KEY,
                            username TEXT NOT NULL UNIQUE,
                            email TEXT UNIQUE,
                            display_name TEXT,
                            tenant_id TEXT NOT NULL DEFAULT 'default_tenant',
                            password_hash TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS auth_sessions (
                            token_hash TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
                            tenant_id TEXT NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
                        ON auth_sessions (user_id, expires_at DESC)
                        """
                    )
                conn.commit()
            self._initialized = True

    @staticmethod
    def _normalize_username(username: str) -> str:
        return username.strip().lower()

    @staticmethod
    def _clean_optional(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _b64decode(value: str) -> bytes:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))

    def _hash_password(self, password: str) -> str:
        iterations = 210_000
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return f"pbkdf2_sha256${iterations}${self._b64encode(salt)}${self._b64encode(digest)}"

    def _verify_password(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, raw_iterations, raw_salt, raw_digest = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(raw_iterations)
            salt = self._b64decode(raw_salt)
            expected = self._b64decode(raw_digest)
        except (TypeError, ValueError):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _row_to_user(row: dict[str, Any]) -> AuthUser:
        return AuthUser(
            id=row["id"],
            username=row["username"],
            email=row.get("email"),
            display_name=row.get("display_name"),
            tenant_id=row["tenant_id"],
            created_at=row.get("created_at"),
        )

    def _create_session(self, user: AuthUser) -> AuthResponse:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + self._token_ttl
        with psycopg.connect(self._postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO auth_sessions (token_hash, user_id, tenant_id, expires_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (self._token_hash(token), user.id, user.tenant_id, expires_at),
                )
            conn.commit()
        return AuthResponse(token=token, user=user)

    def register(self, payload: RegisterRequest) -> AuthResponse:
        self._ensure_initialized()
        username = self._normalize_username(payload.username)
        email = self._clean_optional(payload.email)
        display_name = self._clean_optional(payload.display_name)
        tenant_id = payload.tenant_id.strip() or "default_tenant"
        user_id = f"user_{uuid.uuid4().hex[:16]}"
        password_hash = self._hash_password(payload.password)
        try:
            with psycopg.connect(self._postgres_dsn, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app_users (id, username, email, display_name, tenant_id, password_hash)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id, username, email, display_name, tenant_id, created_at
                        """,
                        (user_id, username, email, display_name, tenant_id, password_hash),
                    )
                    row = cur.fetchone()
                conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="用户名或邮箱已存在",
            ) from exc
        if row is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="注册失败")
        return self._create_session(self._row_to_user(row))

    def login(self, username: str, password: str) -> AuthResponse:
        self._ensure_initialized()
        normalized = self._normalize_username(username)
        with psycopg.connect(self._postgres_dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, username, email, display_name, tenant_id, password_hash, created_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (normalized,),
                )
                row = cur.fetchone()
        if row is None or not self._verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
        return self._create_session(self._row_to_user(row))

    def authenticate_token(self, token: str) -> AuthUser:
        self._ensure_initialized()
        token_hash = self._token_hash(token)
        with psycopg.connect(self._postgres_dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT u.id, u.username, u.email, u.display_name, u.tenant_id, u.created_at, s.expires_at
                    FROM auth_sessions s
                    JOIN app_users u ON u.id = s.user_id
                    WHERE s.token_hash = %s
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if row is None:
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
                if row["expires_at"] <= datetime.now(timezone.utc):
                    cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))
                    conn.commit()
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
        return self._row_to_user(row)

    def logout(self, token: str) -> None:
        self._ensure_initialized()
        with psycopg.connect(self._postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (self._token_hash(token),))
            conn.commit()


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    settings = AppSettings()
    return AuthService(
        postgres_dsn=settings.postgres_dsn,
        token_ttl_hours=settings.auth_token_ttl_hours,
    )


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证信息格式错误")
    return token.strip()


def get_current_user(
    token: str = Depends(get_bearer_token),
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthUser:
    return auth_service.authenticate_token(token)
