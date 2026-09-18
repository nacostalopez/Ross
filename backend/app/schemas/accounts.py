from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["owner", "admin", "viewer"]


class AccountCreate(BaseModel):
    name: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime | None = None


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: Role
    created_at: datetime | None = None


class InviteCreate(BaseModel):
    email: EmailStr
    role: Role


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: Role
    status: str
    created_at: datetime | None = None
    expires_at: datetime
    token: str | None = None  # only populated on creation — never on list


class InviteAcceptIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


class RoleUpdateIn(BaseModel):
    role: Role


class ActivityLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: str
    detail: str
    created_at: datetime
