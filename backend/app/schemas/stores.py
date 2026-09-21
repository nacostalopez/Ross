import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.schemas.accounts import Role

# ISO 4217: always three uppercase Latin letters — rejects things like "AR$"
# (a currency symbol, not a code) that Intl.NumberFormat on the frontend
# throws on rather than silently coercing.
_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")


class StoreCreate(BaseModel):
    name: str
    platform: str
    currency: str = "USD"
    timezone: str = "UTC"

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso_4217_shaped(cls, value: str) -> str:
        if not _CURRENCY_CODE_RE.match(value):
            raise ValueError(f"currency must be a 3-letter ISO 4217 code (e.g. USD, ARS) — got {value!r}")
        return value


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    name: str
    platform: str
    currency: str
    timezone: str
    created_at: datetime | None = None


class StoreWithRoleOut(StoreOut):
    """A store as the requesting user sees it: `effective_role` is the role
    they hold on it — their per-store override if they have one, else their
    account-wide role (see app/dependencies.py::effective_roles_for_stores)."""

    effective_role: str


class StoreCredentialCreate(BaseModel):
    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    # CAPI feedback loop opt-in — see app/services/capi.py. capi_destination_id
    # is the Meta pixel id or Google conversionAction resource name.
    capi_enabled: bool = False
    capi_destination_id: str | None = None


class StoreCredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    provider: str
    expires_at: datetime | None = None
    capi_enabled: bool = False
    capi_destination_id: str | None = None
    # Shopify shop domain / Meta ad account id / Google Ads customer id —
    # populated by each provider's OAuth callback, not settable here.
    provider_account_id: str | None = None


class StoreMemberOut(BaseModel):
    id: UUID
    email: str
    # The account-wide role vs. what actually applies to this one store —
    # store_role is null when there's no override (effective_role ==
    # account_role in that case). See StoreMembership.
    account_role: Role
    store_role: Role | None
    effective_role: Role


class StoreMemberRoleIn(BaseModel):
    # null clears the override, falling back to the account-wide role.
    role: Role | None = None
