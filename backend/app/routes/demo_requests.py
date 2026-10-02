"""The landing's "Pedir demo" form. Public (no account yet, by definition),
so it is rate limited per IP like registration; the request is stored first
and the team notified by email second, best-effort."""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.email import send_email
from app.models import DemoRequest
from app.rate_limit import limiter
from app.schemas.demo_requests import DemoRequestIn, DemoRequestOut

router = APIRouter(prefix="/demo-requests", tags=["demo-requests"])
logger = logging.getLogger("ross.demo_requests")

PLATFORM_LABELS = {
    "shopify": "Shopify",
    "tiendanube": "Tiendanube",
    "mercadolibre": "Mercado Libre",
    "otra": "Otra plataforma",
}


@router.post("", response_model=DemoRequestOut, status_code=201)
@limiter.limit("5/minute")
def create_demo_request(request: Request, payload: DemoRequestIn, db: Session = Depends(get_db)):
    demo_request = DemoRequest(name=payload.name.strip(), email=payload.email, platform=payload.platform)
    db.add(demo_request)
    db.commit()

    try:
        send_email(
            to=settings.demo_request_notify_email,
            subject=f"Nuevo pedido de demo: {demo_request.name}",
            body=(
                f"{demo_request.name} <{demo_request.email}> pidió una demo de ROSS.\n"
                f"Vende en: {PLATFORM_LABELS[demo_request.platform]}.\n\n"
                "Respondele a ese email para coordinar día y horario."
            ),
        )
    except Exception:
        # The request is already saved; a mail problem must not lose the lead or fail the form.
        logger.exception("demo_request_email_failed", extra={"demo_request_id": str(demo_request.id)})

    return {"status": "received"}
