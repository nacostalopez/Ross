from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class PrivacyUserOut(BaseModel):
    id: UUID
    email: str
    role: str
    created_at: datetime | None = None
    terms_accepted_at: datetime | None = None
    terms_version: str | None = None


class PrivacyMembershipOut(BaseModel):
    store_id: UUID
    store_name: str
    role: str


class PrivacyActivityOut(BaseModel):
    action: str
    detail: str
    created_at: datetime | None = None


class PrivacyExportOut(BaseModel):
    """Everything ROSS keeps about the signed-in person, as the right of access asks for."""

    generated_at: datetime
    user: PrivacyUserOut
    account_name: str | None = None
    store_memberships: list[PrivacyMembershipOut]
    activity: list[PrivacyActivityOut]


class DeleteAccountIn(BaseModel):
    password: str
    # The word has to be typed in full, so a stray click can't delete an account.
    confirm: Literal["ELIMINAR"]


class DeleteMeIn(BaseModel):
    password: str
