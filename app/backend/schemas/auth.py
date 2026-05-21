from datetime import datetime

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    id: str
    username: str
    tenant_id: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime | None = None


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    display_name: str | None = Field(default=None, max_length=80)
    tenant_id: str = Field(default="default_tenant", min_length=1, max_length=80)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    token: str
    user: AuthUser


class MessageResponse(BaseModel):
    message: str
