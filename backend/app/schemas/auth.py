from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

# Trimmed and lowercased before validation, so the same address always means the same
# account however it was typed (see app/services/users.py).
NormalizedEmail = Annotated[EmailStr, BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v)]


class RegisterIn(BaseModel):
    account_name: str = Field(min_length=1, max_length=255)
    email: NormalizedEmail
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    email: NormalizedEmail
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str


class ForgotPasswordIn(BaseModel):
    email: NormalizedEmail


class ResetPasswordIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    email: str
    role: str
    created_at: datetime | None = None
