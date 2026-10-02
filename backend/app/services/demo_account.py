"""The public read-only demo ("Ver demo" on the landing).

One account with one USD store full of demo data, and one viewer user whose
email is DEMO_VIEWER_EMAIL. POST /auth/demo hands out sessions for that
user, and get_current_user refuses anything but reads from it, so visitors
can't change what the next visitor sees.

The data is dated relative to "now" (the last 90 days), so it goes stale:
refresh_demo_account() rebuilds it and is meant to run daily —
`python -m app.cli.refresh_demo_account` (see README). It mirrors what the
dashboard's own "Cargar datos de demo" button loads (frontend/app.js).
"""

import random
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Account, Customer, Product, Store, Subscription, User, ad_spend, creative_performance, orders
from app.security import hash_password
from app.services.customers import resolve_customer_id

DEMO_ACCOUNT_NAME = "ROSS · Demo"
DEMO_STORE_NAME = "Tienda de demostración"
DAYS = 90
AD_PLATFORMS = ["meta", "google", "tiktok", "linkedin"]
UTM_SOURCES = AD_PLATFORMS + ["organic"]
CREATIVES = [
    ("meta", "demo-meta-1", "Video — Testimonio cliente"),
    ("meta", "demo-meta-2", "Carrusel — Beneficios producto"),
    ("meta", "demo-meta-3", "Imagen — Oferta 20% OFF"),
    ("google", "demo-google-1", "Búsqueda — Marca"),
    ("google", "demo-google-2", "Búsqueda — Genérico"),
    ("tiktok", "demo-tiktok-1", "Video — Unboxing"),
    ("tiktok", "demo-tiktok-2", "Spark Ad — Reseña de cliente"),
    ("linkedin", "demo-linkedin-1", "Imagen única — Caso de éxito"),
]
# A small repeat-customer pool, each with a fixed acquisition day, so the LTV
# cohorts show 2-3 distinct months (same reasoning as seedDemoData in app.js).
CUSTOMERS = [f"demo-customer-{i}@example.com" for i in range(6)]
ACQUISITION_OFFSETS = [87, 87, 50, 50, 12, 12]


def _order(rng: random.Random, order_id: str, time: datetime, email: str | None) -> dict:
    gross = round(20 + rng.random() * 130, 2)
    return {
        "order_id": order_id,
        "time": time,
        "gross_amount": gross,
        "discounts": 0,
        "shipping_fee": 4.99,
        "payment_gateway_fee": round(gross * 0.029 + 0.3, 2),
        "cogs_total": round(gross * 0.25, 2),
        "currency": "USD",
        "attribution_utm_source": rng.choice(UTM_SOURCES),
        "attribution_utm_campaign": "demo-campaign",
        "customer_email": email,
    }


def _ensure_account(db: Session, viewer_email: str) -> tuple[User, Store]:
    viewer = db.query(User).filter(User.email == viewer_email).first()
    if viewer:
        if viewer.role != "viewer":
            raise ValueError(f"{viewer_email} exists with role {viewer.role}; the demo user must be a viewer")
        store = db.query(Store).filter_by(account_id=viewer.account_id, name=DEMO_STORE_NAME).first()
    else:
        account = Account(name=DEMO_ACCOUNT_NAME)
        db.add(account)
        db.flush()
        db.add(Subscription(account_id=account.id, plan_id="scale", status="active"))
        # Nobody logs in with this password: sessions come from POST /auth/demo.
        viewer = User(account_id=account.id, email=viewer_email, hashed_password=hash_password(secrets.token_urlsafe(32)), role="viewer")
        db.add(viewer)
        store = None
    if store is None:
        store = Store(account_id=viewer.account_id, name=DEMO_STORE_NAME, platform="shopify", currency="USD", timezone="UTC")
        db.add(store)
        db.flush()
        db.add_all(
            [
                Product(store_id=store.id, external_id="sku-1", sku="SKU1", title="T-Shirt", cogs=5.0, shipping_cost=2.0),
                Product(store_id=store.id, external_id="sku-2", sku="SKU2", title="Hoodie", cogs=12.0, shipping_cost=3.0),
            ]
        )
    return viewer, store


def refresh_demo_account(
    db: Session, viewer_email: str, now: datetime | None = None, rng: random.Random | None = None
) -> Store:
    """Create the demo account if missing, then replace its store's data with
    a fresh 90 days ending at `now`. Returns the demo store."""
    now = now or datetime.now(timezone.utc)
    rng = rng or random.Random()
    viewer, store = _ensure_account(db, viewer_email)

    for table in (orders, ad_spend, creative_performance):
        db.execute(delete(table).where(table.c.store_id == store.id))
    db.query(Customer).filter(Customer.store_id == store.id).delete()
    db.flush()

    order_rows = []
    for day in range(DAYS):
        eligible = [c for c, offset in zip(CUSTOMERS, ACQUISITION_OFFSETS) if day <= offset]
        for i in range(3 + rng.randrange(8)):
            time = now - timedelta(days=day, seconds=rng.randrange(86400))
            order_rows.append(_order(rng, f"order-{day}-{i}", time, rng.choice(eligible) if eligible else None))
    for idx, (email, day) in enumerate(zip(CUSTOMERS, ACQUISITION_OFFSETS)):
        order_rows.append(_order(rng, f"order-acq-{idx}", now - timedelta(days=day, seconds=rng.randrange(3600)), email))

    rows = []
    for row in sorted(order_rows, key=lambda r: r["time"]):
        email = row.pop("customer_email")
        row["customer_id"] = resolve_customer_id(db, store.id, email, None, order_time=row["time"])
        rows.append({"store_id": store.id, **row})
    db.execute(pg_insert(orders).values(rows))

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    spend_rows = [
        {
            "time": midnight - timedelta(days=day),
            "store_id": store.id,
            "platform": platform,
            "campaign_id": f"{platform}-demo-campaign",
            "campaign_name": "Demo Campaign",
            "adset_id": "adset-1",
            "spend": round(20 + rng.random() * 60, 2),
            "impressions": 1000 + rng.randrange(4000),
            "clicks": 50 + rng.randrange(250),
        }
        for day in range(DAYS)
        for platform in AD_PLATFORMS
    ]
    db.execute(pg_insert(ad_spend).values(spend_rows))

    creative_rows = []
    for day in range(14):
        for platform, ad_id, ad_name in CREATIVES:
            impressions = 500 + rng.randrange(3000)
            creative_rows.append(
                {
                    "time": midnight - timedelta(days=day),
                    "store_id": store.id,
                    "platform": platform,
                    "campaign_id": f"{platform}-demo-campaign",
                    "campaign_name": "Demo Campaign",
                    "adset_id": "adset-1",
                    "ad_id": ad_id,
                    "ad_name": ad_name,
                    "spend": round(5 + rng.random() * 25, 2),
                    "impressions": impressions,
                    "clicks": int(impressions * (0.005 + rng.random() * 0.04)),
                }
            )
    db.execute(pg_insert(creative_performance).values(creative_rows))

    db.commit()
    return store
