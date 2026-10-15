from typing import Literal

from pydantic import BaseModel, EmailStr, Field

Platform = Literal["shopify", "tiendanube", "mercadolibre", "otra"]


class DemoRequestIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    platform: Platform
    accept_privacy: bool = False


class DemoRequestOut(BaseModel):
    status: str
