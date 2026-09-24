"""Connector routes for OAuth handshakes, webhooks, and sync-status reporting."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.connectors.google import GoogleAdsConnector
from app.connectors.linkedin import LinkedInConnector
from app.connectors.mercadolibre import NON_SALE_STATUSES as ML_NON_SALE_STATUSES
from app.connectors.mercadolibre import SALE_STATUSES as ML_SALE_STATUSES
from app.connectors.mercadolibre import MercadoLibreConnector
from app.connectors.mercadopago import REVERSED_STATUSES as MP_REVERSED_STATUSES
from app.connectors.mercadopago import SALE_STATUSES as MP_SALE_STATUSES
from app.connectors.mercadopago import MercadoPagoConnector, verify_notification_signature
from app.connectors.meta import MetaConnector
from app.connectors.shopify import ShopifyConnector
from app.connectors.tiendanube import TiendanubeConnector
from app.connectors.tiktok import TikTokConnector
from app.database import SessionLocal, get_db
from app.dependencies import get_owned_store, require_store_role
from app.models import (
    ConnectorStatus,
    OAuthState,
    ShopifyWebhookLog,
    Store,
    StoreCredential,
    TiendanubeWebhookLog,
    TokenRefreshAudit,
    User,
)
from app.rate_limit import limiter
from app.security import create_oauth_state_token, decrypt_secret, encrypt_secret, hash_token
from app.services.capi import send_google_purchase_conversion, send_meta_purchase_event
from app.services.connector_status import _upsert_connector_status
from app.services.customers import resolve_customer_id

logger = logging.getLogger("ross.connectors")

router = APIRouter(prefix="/connectors", tags=["connectors"])

# Store-scoped, matching the ownership-check pattern used by every other
# /stores/{store_id}/... router (products, orders, ad_spend, metrics).
health_router = APIRouter(prefix="/stores/{store_id}/connectors", tags=["connectors"])

# OAuth flows should complete in well under this — it only needs to survive
# the user's round trip to the provider's consent screen and back.
OAUTH_STATE_EXPIRE_MINUTES = 10


def _create_oauth_state(db: Session, store_id: UUID, provider: str) -> str:
    """Issue a CSRF state token for an OAuth handshake, persisting its hash
    so the matching callback can prove it followed this store's own
    auth-url step rather than being a forged/replayed request."""
    raw_token = create_oauth_state_token()
    db.add(
        OAuthState(
            store_id=store_id,
            provider=provider,
            token_hash=hash_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_EXPIRE_MINUTES),
        )
    )
    db.commit()
    return raw_token


def _consume_oauth_state(db: Session, store_id: UUID, provider: str, state: Optional[str]) -> None:
    """Validate and burn a one-time OAuth state token. Rejects a missing,
    unknown, expired, already-used, or wrong store/provider token — this is
    what actually closes the CSRF gap; issuing the token alone doesn't."""
    if not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state token")

    row = db.query(OAuthState).filter_by(token_hash=hash_token(state)).first()
    now = datetime.now(timezone.utc)
    if (
        not row
        or row.store_id != store_id
        or row.provider != provider
        or row.consumed_at is not None
        or row.expires_at < now
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state token")

    row.consumed_at = now
    db.commit()


@router.post("/shopify/auth-url")
def get_shopify_auth_url(
    shop_domain: str,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get Shopify OAuth authorization URL.

    Args:
        shop_domain: Shopify shop domain (e.g., "mystore.myshopify.com")

    Returns:
        Authorization URL to redirect user to
    """
    state_token = _create_oauth_state(db, store.id, "shopify")
    connector = ShopifyConnector(str(store.id), shop_domain)
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/shopify/callback")
def shopify_oauth_callback(
    code: str,
    shop: str,
    state: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle Shopify OAuth callback.

    Args:
        code: Authorization code from Shopify
        shop: Shop domain from Shopify
        state: State token for CSRF validation

    Returns:
        Success message
    """
    store_id = store.id
    _consume_oauth_state(db, store_id, "shopify", state)

    try:
        connector = ShopifyConnector(str(store_id), shop)
        token = connector.exchange_auth_code(code, connector.settings.shopify_redirect_uri, shop)

        # Store encrypted token in database
        encrypted_token = encrypt_secret(token.access_token)

        # Check if credential already exists
        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="shopify",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            existing.refresh_token = encrypt_secret(token.refresh_token) if token.refresh_token else None
            existing.expires_at = token.expires_at
            existing.provider_account_id = shop
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="shopify",
                access_token=encrypted_token,
                refresh_token=encrypt_secret(token.refresh_token) if token.refresh_token else None,
                expires_at=token.expires_at,
                provider_account_id=shop,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "shopify", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to Shopify",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "shopify", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with Shopify: {str(e)}",
        )


@router.post("/shopify/webhook/{store_id}")
@limiter.limit("120/minute")
async def shopify_webhook(
    request: Request,
    store_id: UUID,
    background_tasks: BackgroundTasks,
    x_shopify_hmac_sha256: str = Header(None),
    x_shopify_shop_api_version: str = Header(None),
    x_shopify_topic: str = Header(None),
    db: Session = Depends(get_db),
):
    """Receive Shopify webhook.

    Shopify sends events like orders/create, products/update, etc.
    We validate the signature and process the event. Every call (accepted or
    rejected) is logged to shopify_webhooks_log, and connector_status is
    updated so GET /stores/{id}/connectors/health reflects the outcome.

    Args:
        store_id: ID of the store
        request: HTTP request with body
        x_shopify_hmac_sha256: HMAC signature from Shopify
        x_shopify_shop_api_version: API version from Shopify
        x_shopify_topic: Event topic (e.g., "orders/create")

    Returns:
        Acknowledgment
    """
    # Verify store exists
    store = db.get(Store, store_id)
    if not store or store.platform != "shopify":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")

    # Get raw body for signature validation
    body = await request.body()
    body_str = body.decode("utf-8")
    topic = x_shopify_topic or "unknown"

    # Validate signature
    connector = ShopifyConnector(str(store_id))
    if not connector.validate_webhook_signature(body_str, x_shopify_hmac_sha256):
        db.add(
            ShopifyWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=False,
                status="rejected",
            )
        )
        db.commit()
        _upsert_connector_status(db, store_id, "shopify", synced=True, success=False, error="Invalid webhook signature")
        logger.warning(
            "shopify_webhook_rejected",
            extra={"store_id": str(store_id), "topic": topic, "reason": "invalid_signature"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        data = json.loads(body_str)

        # Process based on event type
        if topic in ("orders/create", "orders/updated"):
            # Convert to standardized format
            order_data = connector.process_webhook(topic, data)

            # Ingest into orders table
            from app.models import orders as orders_table

            order_data.pop("net_profit", None)  # generated column — Postgres computes this
            customer_email = order_data.pop("customer_email", None)
            customer_phone = order_data.pop("customer_phone", None)
            external_customer_id = order_data.pop("external_customer_id", None)
            order_data["customer_id"] = resolve_customer_id(
                db,
                store_id,
                customer_email,
                customer_phone,
                external_customer_id,
                order_time=order_data["time"],
            )
            row = {"store_id": store_id, **order_data}
            stmt = pg_insert(orders_table).values([row])
            update_cols = {
                c.name: stmt.excluded[c.name]
                for c in orders_table.c
                if c.name not in ("time", "store_id", "order_id", "net_profit")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["store_id", "order_id", "time"],
                set_=update_cols,
            )
            db.execute(stmt)
            background_tasks.add_task(send_meta_purchase_event, store_id, row["order_id"], row)
            background_tasks.add_task(send_google_purchase_conversion, store_id, row["order_id"], row)

        db.add(
            ShopifyWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=True,
                status="processed",
            )
        )
        db.commit()
        _upsert_connector_status(db, store_id, "shopify", synced=True, success=True)
        logger.info(
            "shopify_webhook_processed",
            extra={"store_id": str(store_id), "topic": topic},
        )

        return {"status": "received"}
    except Exception as e:
        db.rollback()
        db.add(
            ShopifyWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=True,
                status="error",
                error_message=str(e),
            )
        )
        db.commit()
        _upsert_connector_status(db, store_id, "shopify", synced=True, success=False, error=str(e))
        logger.error(
            "shopify_webhook_processing_failed",
            extra={"store_id": str(store_id), "topic": topic, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process Shopify webhook: {str(e)}",
        )


# ============================================================================
# Meta (Facebook) Ads Connectors
# ============================================================================


@router.post("/meta/auth-url")
def get_meta_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get Meta OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "meta")
    connector = MetaConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/meta/callback")
def meta_oauth_callback(
    code: str,
    state: Optional[str] = None,
    ad_account_id: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle Meta OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "meta", state)

    try:
        connector = MetaConnector(str(store_id), ad_account_id)
        token = connector.exchange_auth_code(code, connector.settings.meta_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="meta",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            if ad_account_id:
                existing.provider_account_id = ad_account_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="meta",
                access_token=encrypted_token,
                provider_account_id=ad_account_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "meta", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to Meta",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "meta", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with Meta: {str(e)}",
        )


@router.post("/meta/sync-ad-spend")
def sync_meta_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad spend data from Meta."""
    store_id = store.id

    # Get Meta credentials
    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="meta",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Meta credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)
        connector = MetaConnector(str(store_id), credential.provider_account_id)

        spend_records = connector.fetch_ad_spend(access_token, start_date, end_date)

        # Ingest into ad_spend table
        from app.models import ad_spend as ad_spend_table

        rows = [{"store_id": store_id, **record} for record in spend_records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "meta", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "meta", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Meta ad spend: {str(e)}",
        )


@router.post("/meta/sync-creative-performance")
def sync_meta_creative_performance(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad-level (creative) performance from Meta."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="meta",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Meta credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)
        connector = MetaConnector(str(store_id), credential.provider_account_id)

        creative_records = connector.fetch_creative_performance(access_token, start_date, end_date)

        from app.models import creative_performance as creative_performance_table

        rows = [{"store_id": store_id, **record} for record in creative_records]
        if rows:
            db.execute(insert(creative_performance_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "meta", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "meta", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Meta creative performance: {str(e)}",
        )


# ============================================================================
# Google Ads Connectors
# ============================================================================


@router.post("/google/auth-url")
def get_google_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get Google OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "google")
    connector = GoogleAdsConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/google/callback")
def google_oauth_callback(
    code: str,
    state: Optional[str] = None,
    customer_id: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle Google OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "google", state)

    try:
        connector = GoogleAdsConnector(str(store_id), customer_id)
        token = connector.exchange_auth_code(code, connector.settings.google_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)
        encrypted_refresh = encrypt_secret(token.refresh_token) if token.refresh_token else None

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="google",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            existing.refresh_token = encrypted_refresh
            existing.expires_at = token.expires_at
            if customer_id:
                existing.provider_account_id = customer_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="google",
                access_token=encrypted_token,
                refresh_token=encrypted_refresh,
                expires_at=token.expires_at,
                provider_account_id=customer_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "google", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to Google Ads",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "google", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with Google: {str(e)}",
        )


@router.post("/google/sync-ad-spend")
def sync_google_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad spend data from Google Ads."""
    store_id = store.id

    # Get Google credentials
    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="google",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)

        # Check if token is expired and refresh if needed
        if credential.expires_at and datetime.now(timezone.utc) > credential.expires_at:
            refresh_token = decrypt_secret(credential.refresh_token) if credential.refresh_token else None
            if refresh_token:
                try:
                    connector = GoogleAdsConnector(str(store_id))
                    new_token = connector.refresh_access_token(refresh_token)
                    access_token = new_token.access_token
                    credential.access_token = encrypt_secret(access_token)
                    credential.expires_at = new_token.expires_at
                    db.commit()
                    db.add(TokenRefreshAudit(id=uuid4(), store_id=store_id, provider="google", success=True))
                    db.commit()
                except Exception as refresh_error:
                    db.add(
                        TokenRefreshAudit(
                            id=uuid4(),
                            store_id=store_id,
                            provider="google",
                            success=False,
                            error_message=str(refresh_error),
                        )
                    )
                    db.commit()
                    raise

        connector = GoogleAdsConnector(str(store_id), credential.provider_account_id)
        spend_records = connector.fetch_ad_spend(access_token, start_date, end_date)

        # Ingest into ad_spend table
        from app.models import ad_spend as ad_spend_table

        rows = [{"store_id": store_id, **record} for record in spend_records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "google", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "google", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Google ad spend: {str(e)}",
        )


@router.post("/google/sync-creative-performance")
def sync_google_creative_performance(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad-level (creative) performance from Google Ads."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="google",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)

        if credential.expires_at and datetime.now(timezone.utc) > credential.expires_at:
            refresh_token = decrypt_secret(credential.refresh_token) if credential.refresh_token else None
            if refresh_token:
                try:
                    connector = GoogleAdsConnector(str(store_id))
                    new_token = connector.refresh_access_token(refresh_token)
                    access_token = new_token.access_token
                    credential.access_token = encrypt_secret(access_token)
                    credential.expires_at = new_token.expires_at
                    db.commit()
                    db.add(TokenRefreshAudit(id=uuid4(), store_id=store_id, provider="google", success=True))
                    db.commit()
                except Exception as refresh_error:
                    db.add(
                        TokenRefreshAudit(
                            id=uuid4(),
                            store_id=store_id,
                            provider="google",
                            success=False,
                            error_message=str(refresh_error),
                        )
                    )
                    db.commit()
                    raise

        connector = GoogleAdsConnector(str(store_id), credential.provider_account_id)
        creative_records = connector.fetch_creative_performance(access_token, start_date, end_date)

        from app.models import creative_performance as creative_performance_table

        rows = [{"store_id": store_id, **record} for record in creative_records]
        if rows:
            db.execute(insert(creative_performance_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "google", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "google", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Google creative performance: {str(e)}",
        )


# ============================================================================
# TikTok Ads Connectors
# ============================================================================


@router.post("/tiktok/auth-url")
def get_tiktok_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get TikTok OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "tiktok")
    connector = TikTokConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/tiktok/callback")
def tiktok_oauth_callback(
    code: str,
    state: Optional[str] = None,
    advertiser_id: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle TikTok OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "tiktok", state)

    try:
        connector = TikTokConnector(str(store_id), advertiser_id)
        token = connector.exchange_auth_code(code, connector.settings.tiktok_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="tiktok",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            if advertiser_id:
                existing.provider_account_id = advertiser_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="tiktok",
                access_token=encrypted_token,
                provider_account_id=advertiser_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "tiktok", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to TikTok",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "tiktok", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with TikTok: {str(e)}",
        )


@router.post("/tiktok/sync-ad-spend")
def sync_tiktok_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad spend data from TikTok."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="tiktok",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TikTok credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)
        connector = TikTokConnector(str(store_id), credential.provider_account_id)

        spend_records = connector.fetch_ad_spend(access_token, start_date, end_date)

        from app.models import ad_spend as ad_spend_table

        rows = [{"store_id": store_id, **record} for record in spend_records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "tiktok", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "tiktok", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync TikTok ad spend: {str(e)}",
        )


@router.post("/tiktok/sync-creative-performance")
def sync_tiktok_creative_performance(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad-level (creative) performance from TikTok."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="tiktok",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TikTok credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)
        connector = TikTokConnector(str(store_id), credential.provider_account_id)

        creative_records = connector.fetch_creative_performance(access_token, start_date, end_date)

        from app.models import creative_performance as creative_performance_table

        rows = [{"store_id": store_id, **record} for record in creative_records]
        if rows:
            db.execute(insert(creative_performance_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "tiktok", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "tiktok", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync TikTok creative performance: {str(e)}",
        )


# ============================================================================
# LinkedIn Ads Connectors
# ============================================================================


@router.post("/linkedin/auth-url")
def get_linkedin_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get LinkedIn OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "linkedin")
    connector = LinkedInConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/linkedin/callback")
def linkedin_oauth_callback(
    code: str,
    state: Optional[str] = None,
    ad_account_id: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle LinkedIn OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "linkedin", state)

    try:
        connector = LinkedInConnector(str(store_id), ad_account_id)
        token = connector.exchange_auth_code(code, connector.settings.linkedin_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)
        encrypted_refresh = encrypt_secret(token.refresh_token) if token.refresh_token else None

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="linkedin",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            existing.refresh_token = encrypted_refresh
            existing.expires_at = token.expires_at
            if ad_account_id:
                existing.provider_account_id = ad_account_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="linkedin",
                access_token=encrypted_token,
                refresh_token=encrypted_refresh,
                expires_at=token.expires_at,
                provider_account_id=ad_account_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "linkedin", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to LinkedIn",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "linkedin", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with LinkedIn: {str(e)}",
        )


@router.post("/linkedin/sync-ad-spend")
def sync_linkedin_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad spend data from LinkedIn."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="linkedin",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LinkedIn credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)

        if credential.expires_at and datetime.now(timezone.utc) > credential.expires_at:
            refresh_token = decrypt_secret(credential.refresh_token) if credential.refresh_token else None
            if refresh_token:
                try:
                    connector = LinkedInConnector(str(store_id))
                    new_token = connector.refresh_access_token(refresh_token)
                    access_token = new_token.access_token
                    credential.access_token = encrypt_secret(access_token)
                    credential.expires_at = new_token.expires_at
                    db.commit()
                    db.add(TokenRefreshAudit(id=uuid4(), store_id=store_id, provider="linkedin", success=True))
                    db.commit()
                except Exception as refresh_error:
                    db.add(
                        TokenRefreshAudit(
                            id=uuid4(),
                            store_id=store_id,
                            provider="linkedin",
                            success=False,
                            error_message=str(refresh_error),
                        )
                    )
                    db.commit()
                    raise

        connector = LinkedInConnector(str(store_id), credential.provider_account_id)
        spend_records = connector.fetch_ad_spend(access_token, start_date, end_date)

        from app.models import ad_spend as ad_spend_table

        rows = [{"store_id": store_id, **record} for record in spend_records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "linkedin", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "linkedin", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync LinkedIn ad spend: {str(e)}",
        )


@router.post("/linkedin/sync-creative-performance")
def sync_linkedin_creative_performance(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad-level (creative) performance from LinkedIn."""
    store_id = store.id

    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="linkedin",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LinkedIn credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)

        if credential.expires_at and datetime.now(timezone.utc) > credential.expires_at:
            refresh_token = decrypt_secret(credential.refresh_token) if credential.refresh_token else None
            if refresh_token:
                try:
                    connector = LinkedInConnector(str(store_id))
                    new_token = connector.refresh_access_token(refresh_token)
                    access_token = new_token.access_token
                    credential.access_token = encrypt_secret(access_token)
                    credential.expires_at = new_token.expires_at
                    db.commit()
                    db.add(TokenRefreshAudit(id=uuid4(), store_id=store_id, provider="linkedin", success=True))
                    db.commit()
                except Exception as refresh_error:
                    db.add(
                        TokenRefreshAudit(
                            id=uuid4(),
                            store_id=store_id,
                            provider="linkedin",
                            success=False,
                            error_message=str(refresh_error),
                        )
                    )
                    db.commit()
                    raise

        connector = LinkedInConnector(str(store_id), credential.provider_account_id)
        creative_records = connector.fetch_creative_performance(access_token, start_date, end_date)

        from app.models import creative_performance as creative_performance_table

        rows = [{"store_id": store_id, **record} for record in creative_records]
        if rows:
            db.execute(insert(creative_performance_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "linkedin", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "linkedin", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync LinkedIn creative performance: {str(e)}",
        )


# ============================================================================
# Tiendanube Connectors
# ============================================================================


@router.post("/tiendanube/auth-url")
def get_tiendanube_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get Tiendanube OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "tiendanube")
    connector = TiendanubeConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/tiendanube/callback")
def tiendanube_oauth_callback(
    code: str,
    state: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle Tiendanube OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "tiendanube", state)

    try:
        connector = TiendanubeConnector(str(store_id))
        token = connector.exchange_auth_code(code, connector.settings.tiendanube_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="tiendanube",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            existing.provider_account_id = connector.tn_store_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="tiendanube",
                access_token=encrypted_token,
                provider_account_id=connector.tn_store_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "tiendanube", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to Tiendanube",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "tiendanube", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with Tiendanube: {str(e)}",
        )


@router.post("/tiendanube/webhook/{store_id}")
@limiter.limit("120/minute")
async def tiendanube_webhook(
    request: Request,
    store_id: UUID,
    background_tasks: BackgroundTasks,
    x_linkedstore_hmac_sha256: str = Header(None),
    x_linkedstore_topic: str = Header(None),
    db: Session = Depends(get_db),
):
    """Receive Tiendanube webhook.

    Tiendanube sends events like order/created, order/updated, etc.
    We validate the signature and process the event. Every call (accepted or
    rejected) is logged to tiendanube_webhooks_log, and connector_status is
    updated so GET /stores/{id}/connectors/health reflects the outcome.

    Args:
        store_id: ID of the store
        request: HTTP request with body
        x_linkedstore_hmac_sha256: HMAC signature from Tiendanube
        x_linkedstore_topic: Event topic (e.g., "order/created")

    Returns:
        Acknowledgment
    """
    # Verify store exists
    store = db.get(Store, store_id)
    if not store or store.platform != "tiendanube":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")

    # Get raw body for signature validation
    body = await request.body()
    body_str = body.decode("utf-8")
    topic = x_linkedstore_topic or "unknown"

    # Validate signature
    connector = TiendanubeConnector(str(store_id))
    if not connector.validate_webhook_signature(body_str, x_linkedstore_hmac_sha256):
        db.add(
            TiendanubeWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=False,
                status="rejected",
            )
        )
        db.commit()
        _upsert_connector_status(
            db, store_id, "tiendanube", synced=True, success=False, error="Invalid webhook signature"
        )
        logger.warning(
            "tiendanube_webhook_rejected",
            extra={"store_id": str(store_id), "topic": topic, "reason": "invalid_signature"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        data = json.loads(body_str)

        # Process based on event type
        if topic in ("order/created", "order/updated"):
            # Convert to standardized format
            order_data = connector.process_webhook(topic, data)

            # Ingest into orders table
            from app.models import orders as orders_table

            order_data.pop("net_profit", None)  # generated column — Postgres computes this
            customer_email = order_data.pop("customer_email", None)
            customer_phone = order_data.pop("customer_phone", None)
            external_customer_id = order_data.pop("external_customer_id", None)
            order_data["customer_id"] = resolve_customer_id(
                db,
                store_id,
                customer_email,
                customer_phone,
                external_customer_id,
                order_time=order_data["time"],
            )
            row = {"store_id": store_id, **order_data}
            stmt = pg_insert(orders_table).values([row])
            update_cols = {
                c.name: stmt.excluded[c.name]
                for c in orders_table.c
                if c.name not in ("time", "store_id", "order_id", "net_profit")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["store_id", "order_id", "time"],
                set_=update_cols,
            )
            db.execute(stmt)
            background_tasks.add_task(send_meta_purchase_event, store_id, row["order_id"], row)
            background_tasks.add_task(send_google_purchase_conversion, store_id, row["order_id"], row)

        db.add(
            TiendanubeWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=True,
                status="processed",
            )
        )
        db.commit()
        _upsert_connector_status(db, store_id, "tiendanube", synced=True, success=True)
        logger.info(
            "tiendanube_webhook_processed",
            extra={"store_id": str(store_id), "topic": topic},
        )

        return {"status": "received"}
    except Exception as e:
        db.rollback()
        db.add(
            TiendanubeWebhookLog(
                id=uuid4(),
                store_id=store_id,
                topic=topic,
                signature_valid=True,
                status="error",
                error_message=str(e),
            )
        )
        db.commit()
        _upsert_connector_status(db, store_id, "tiendanube", synced=True, success=False, error=str(e))
        logger.error(
            "tiendanube_webhook_processing_failed",
            extra={"store_id": str(store_id), "topic": topic, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to process Tiendanube webhook: {str(e)}",
        )


# ============================================================================
# MercadoPago Connectors
# ============================================================================


@router.post("/mercadopago/auth-url")
def get_mercadopago_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get MercadoPago OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "mercadopago")
    connector = MercadoPagoConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/mercadopago/callback")
def mercadopago_oauth_callback(
    code: str,
    state: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle MercadoPago OAuth callback."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "mercadopago", state)

    try:
        connector = MercadoPagoConnector(str(store_id))
        token = connector.exchange_auth_code(code, connector.settings.mercadopago_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)
        encrypted_refresh = encrypt_secret(token.refresh_token) if token.refresh_token else None

        existing = (
            db.query(StoreCredential)
            .filter_by(
                store_id=store_id,
                provider="mercadopago",
            )
            .first()
        )

        if existing:
            existing.access_token = encrypted_token
            existing.refresh_token = encrypted_refresh
            existing.expires_at = token.expires_at
            existing.provider_account_id = connector.seller_id
        else:
            credential = StoreCredential(
                id=uuid4(),
                store_id=store_id,
                provider="mercadopago",
                access_token=encrypted_token,
                refresh_token=encrypted_refresh,
                expires_at=token.expires_at,
                provider_account_id=connector.seller_id,
            )
            db.add(credential)

        db.commit()
        _upsert_connector_status(db, store_id, "mercadopago", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to MercadoPago",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "mercadopago", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with MercadoPago: {str(e)}",
        )


@router.post("/mercadopago/sync-payments")
def sync_mercadopago_payments(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync the merchant's Mercado Pago payments.

    On a "mercadopago" store each approved payment becomes an order; on any
    other store payments only fill in the processing fee of the order they
    paid for (see app/connectors/mercadopago.py's module docstring).
    """
    credential = _require_credential(db, store.id, "mercadopago", "MercadoPago")

    try:
        connector = MercadoPagoConnector(str(store.id), credential.provider_account_id)
        access_token = _fresh_access_token(db, credential, connector)
        payments = connector.fetch_payments(access_token, start_date, end_date)
        result = _apply_mercadopago_payments(db, store, connector, payments)
        _upsert_connector_status(db, store.id, "mercadopago", synced=True, success=True)
        return {"status": "success", **result}
    except Exception as e:
        db.rollback()
        _upsert_connector_status(db, store.id, "mercadopago", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync MercadoPago payments: {str(e)}",
        )


@router.post("/mercadopago/notifications")
@limiter.limit("600/minute")
async def mercadopago_notification(
    request: Request,
    background_tasks: BackgroundTasks,
    x_signature: str = Header(None),
    x_request_id: str = Header(None),
):
    """Receive a Mercado Pago payment notification for a connected merchant.

    One URL for the whole app (configured in Mercado Pago's Webhooks panel,
    "Pagos" event): the body's user_id says which merchant it's about. The
    signature is checked here; the payment itself is re-fetched with that
    merchant's own token in the background, never read from the body.
    Mercado Pago wants a 2xx within 22 seconds or it retries.
    """
    data_id = request.query_params.get("data.id")
    connector = MercadoPagoConnector("")
    if not verify_notification_signature(
        connector.settings.mercadopago_webhook_secret, x_signature, x_request_id, data_id
    ):
        logger.warning("mercadopago_notification_rejected", extra={"reason": "invalid_signature"})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature")

    try:
        body = await request.json()
    except ValueError:
        body = {}
    topic = request.query_params.get("type") or body.get("type")
    user_id = body.get("user_id")

    if topic == "payment" and user_id and data_id:
        background_tasks.add_task(_process_mercadopago_payment_notification, str(user_id), str(data_id))
    return {"status": "received"}


def _process_mercadopago_payment_notification(seller_id: str, payment_id: str) -> None:
    db = SessionLocal()
    try:
        _sync_mercadopago_payment(db, seller_id, payment_id)
    finally:
        db.close()


def _sync_mercadopago_payment(db: Session, seller_id: str, payment_id: str) -> None:
    """Apply one payment to every store connected to that Mercado Pago account."""
    credentials = db.query(StoreCredential).filter_by(provider="mercadopago", provider_account_id=seller_id).all()
    for credential in credentials:
        store = db.get(Store, credential.store_id)
        try:
            connector = MercadoPagoConnector(str(store.id), seller_id)
            access_token = _fresh_access_token(db, credential, connector)
            payment = connector.fetch_payment(access_token, payment_id)
            _apply_mercadopago_payments(db, store, connector, [payment])
            _upsert_connector_status(db, store.id, "mercadopago", synced=True, success=True)
        except Exception as e:
            db.rollback()
            _upsert_connector_status(db, store.id, "mercadopago", synced=True, success=False, error=str(e))
            logger.error(
                "mercadopago_notification_failed",
                extra={"store_id": str(store.id), "payment_id": payment_id, "error": str(e)},
            )


def _apply_mercadopago_payments(
    db: Session, store: Store, connector: MercadoPagoConnector, payments: list[dict]
) -> dict:
    """Write payments into the store's orders — as orders of their own on a
    "mercadopago" store, as fees on existing orders anywhere else."""
    from app.models import orders as orders_table

    payments = [p for p in payments if not connector.is_mercadolibre_payment(p)]
    approved = [p for p in payments if p.get("status") in MP_SALE_STATUSES]

    if store.platform == "mercadopago":
        _upsert_orders(db, store.id, [connector.payment_to_order(p) for p in approved])
        removed = _delete_orders(
            db, store.id, [f"mp:{p['id']}" for p in payments if p.get("status") in MP_REVERSED_STATUSES]
        )
        return {"mode": "orders", "orders_synced": len(approved), "orders_removed": removed}

    # external_reference is whatever the checkout that created the payment
    # put there — a platform's Mercado Pago integration normally sets its
    # own order id, which is what orders.order_id holds. Unverified against
    # real Shopify/Tiendanube payments (no connected account here yet); a
    # payment with no match, or no reference, is simply left alone.
    matched = 0
    for payment in approved:
        reference = payment.get("external_reference")
        if not reference:
            continue
        result = db.execute(
            orders_table.update()
            .where(orders_table.c.store_id == store.id, orders_table.c.order_id == str(reference))
            .values(payment_gateway_fee=connector.processing_fee(payment))
        )
        matched += result.rowcount
    db.commit()
    return {"mode": "fees", "payments_seen": len(approved), "orders_updated": matched}


# ============================================================================
# Mercado Libre Connectors
# ============================================================================


@router.post("/mercadolibre/auth-url")
def get_mercadolibre_auth_url(
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Get Mercado Libre OAuth authorization URL."""
    state_token = _create_oauth_state(db, store.id, "mercadolibre")
    connector = MercadoLibreConnector(str(store.id))
    auth_url = connector.get_oauth_url(state_token)

    return {
        "auth_url": auth_url,
        "state": state_token,
    }


@router.post("/mercadolibre/callback")
def mercadolibre_oauth_callback(
    code: str,
    state: Optional[str] = None,
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Handle Mercado Libre OAuth callback. The seller's user_id (from the
    token response) is kept as provider_account_id — every orders/ads call
    and every notification lookup keys on it."""
    store_id = store.id
    _consume_oauth_state(db, store_id, "mercadolibre", state)

    try:
        connector = MercadoLibreConnector(str(store_id))
        token = connector.exchange_auth_code(code, connector.settings.mercadolibre_redirect_uri)

        encrypted_token = encrypt_secret(token.access_token)
        encrypted_refresh = encrypt_secret(token.refresh_token) if token.refresh_token else None

        existing = db.query(StoreCredential).filter_by(store_id=store_id, provider="mercadolibre").first()

        if existing:
            existing.access_token = encrypted_token
            existing.refresh_token = encrypted_refresh
            existing.expires_at = token.expires_at
            existing.provider_account_id = connector.seller_id
        else:
            db.add(
                StoreCredential(
                    id=uuid4(),
                    store_id=store_id,
                    provider="mercadolibre",
                    access_token=encrypted_token,
                    refresh_token=encrypted_refresh,
                    expires_at=token.expires_at,
                    provider_account_id=connector.seller_id,
                )
            )

        db.commit()
        _upsert_connector_status(db, store_id, "mercadolibre", success=True)

        return {
            "status": "success",
            "message": f"Store {store.name} connected to Mercado Libre",
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "mercadolibre", success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to authenticate with Mercado Libre: {str(e)}",
        )


@router.post("/mercadolibre/sync-orders")
def sync_mercadolibre_orders(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Backfill Mercado Libre orders created in [start_date, end_date].

    Paid orders are upserted; cancelled ones are removed in case an earlier
    sync or notification ingested them while they were still paid. New
    orders after this arrive through /mercadolibre/notifications.
    """
    credential = _require_credential(db, store.id, "mercadolibre", "Mercado Libre")

    try:
        connector = MercadoLibreConnector(str(store.id), credential.provider_account_id)
        access_token = _fresh_access_token(db, credential, connector)
        raw_orders = connector.fetch_orders(access_token, start_date, end_date)

        sales = [connector.order_to_row(o) for o in raw_orders if o.get("status") in ML_SALE_STATUSES]
        cancelled = [str(o["id"]) for o in raw_orders if o.get("status") in ML_NON_SALE_STATUSES]
        _upsert_orders(db, store.id, sales)
        removed = _delete_orders(db, store.id, cancelled)

        _upsert_connector_status(db, store.id, "mercadolibre", synced=True, success=True)
        return {"status": "success", "orders_synced": len(sales), "orders_removed": removed}
    except Exception as e:
        db.rollback()
        _upsert_connector_status(db, store.id, "mercadolibre", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Mercado Libre orders: {str(e)}",
        )


@router.post("/mercadolibre/sync-ad-spend")
def sync_mercadolibre_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync daily Product Ads spend per campaign.

    Idempotent, unlike the other ad platforms' plain inserts: the window's
    existing "mercadolibre" rows are replaced, so re-running a sync (Mercado
    Ads revises recent days until 10:00 GMT-3) never double-counts. Only the
    days Mercado Ads can still report on (last 90) are touched — older rows
    are kept as they are.
    """
    from app.models import ad_spend as ad_spend_table

    credential = _require_credential(db, store.id, "mercadolibre", "Mercado Libre")

    try:
        connector = MercadoLibreConnector(str(store.id), credential.provider_account_id)
        access_token = _fresh_access_token(db, credential, connector)
        advertiser_id = connector.fetch_advertiser_id(access_token)
        window = connector.ads_window(start_date, end_date)

        if not advertiser_id or not window:
            _upsert_connector_status(db, store.id, "mercadolibre", synced=True, success=True)
            return {"status": "success", "records_synced": 0, "product_ads_enabled": bool(advertiser_id)}

        records = connector.fetch_ad_spend(access_token, start_date, end_date, advertiser_id=advertiser_id)
        window_start = datetime.combine(window[0], datetime.min.time(), tzinfo=timezone.utc)
        window_end = datetime.combine(window[1], datetime.max.time(), tzinfo=timezone.utc)
        db.execute(
            ad_spend_table.delete().where(
                ad_spend_table.c.store_id == store.id,
                ad_spend_table.c.platform == "mercadolibre",
                ad_spend_table.c.time.between(window_start, window_end),
            )
        )
        rows = [{"store_id": store.id, **record} for record in records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
        db.commit()

        _upsert_connector_status(db, store.id, "mercadolibre", synced=True, success=True)
        return {"status": "success", "records_synced": len(rows), "product_ads_enabled": True}
    except Exception as e:
        db.rollback()
        _upsert_connector_status(db, store.id, "mercadolibre", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync Mercado Libre ad spend: {str(e)}",
        )


@router.post("/mercadolibre/notifications")
@limiter.limit("600/minute")
async def mercadolibre_notification(request: Request, background_tasks: BackgroundTasks):
    """Receive a Mercado Libre notification (topic orders_v2).

    One URL for the whole app (the "Notifications callback URL" in the
    Mercado Libre app settings). Mercado Libre wants a 200 within 500 ms
    and retries otherwise, so this only checks the payload names our app
    and hands the rest to a background task, which re-fetches the order
    with the seller's own token — the payload is never trusted for data.
    """
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    connector = MercadoLibreConnector("")
    if not connector.validate_webhook_signature("", str(payload.get("application_id") or "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown application")

    resource = payload.get("resource") or ""
    user_id = payload.get("user_id")
    if payload.get("topic") == "orders_v2" and user_id and resource.startswith("/orders/"):
        background_tasks.add_task(_process_mercadolibre_order_notification, str(user_id), resource)
    return {"status": "received"}


def _process_mercadolibre_order_notification(seller_id: str, resource: str) -> None:
    db = SessionLocal()
    try:
        _sync_mercadolibre_order(db, seller_id, resource)
    finally:
        db.close()


def _sync_mercadolibre_order(db: Session, seller_id: str, resource: str) -> None:
    """Re-fetch one order and apply it to every store connected to that seller."""
    credentials = db.query(StoreCredential).filter_by(provider="mercadolibre", provider_account_id=seller_id).all()
    for credential in credentials:
        store_id = credential.store_id
        try:
            connector = MercadoLibreConnector(str(store_id), seller_id)
            access_token = _fresh_access_token(db, credential, connector)
            order = connector.fetch_order(access_token, resource)
            if order.get("status") in ML_SALE_STATUSES:
                _upsert_orders(db, store_id, [connector.order_to_row(order)])
            elif order.get("status") in ML_NON_SALE_STATUSES:
                _delete_orders(db, store_id, [str(order["id"])])
            _upsert_connector_status(db, store_id, "mercadolibre", synced=True, success=True)
        except Exception as e:
            db.rollback()
            _upsert_connector_status(db, store_id, "mercadolibre", synced=True, success=False, error=str(e))
            logger.error(
                "mercadolibre_notification_failed",
                extra={"store_id": str(store_id), "resource": resource, "error": str(e)},
            )


# ============================================================================
# Helpers shared by the Mercado Libre / Mercado Pago routes
# ============================================================================

# Refresh a bit before the real expiry so a token can't die mid-sync.
TOKEN_REFRESH_MARGIN = timedelta(minutes=5)


def _require_credential(db: Session, store_id: UUID, provider: str, label: str) -> StoreCredential:
    credential = db.query(StoreCredential).filter_by(store_id=store_id, provider=provider).first()
    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} credentials not configured for this store",
        )
    return credential


def _fresh_access_token(db: Session, credential: StoreCredential, connector) -> str:
    """The credential's decrypted access token, refreshed first if it's
    about to expire.

    The refresh runs with the row locked (SELECT ... FOR UPDATE): Mercado
    Libre refresh tokens are single-use, so two concurrent refreshes (a
    manual sync racing a notification) would leave the loser presenting a
    spent token — and the store disconnected for good.
    """
    now = datetime.now(timezone.utc)
    if not credential.expires_at or credential.expires_at - TOKEN_REFRESH_MARGIN > now:
        return decrypt_secret(credential.access_token)

    locked = db.query(StoreCredential).filter_by(id=credential.id).with_for_update().populate_existing().one()
    # Someone else may have refreshed while this waited on the lock.
    if not locked.refresh_token or (locked.expires_at and locked.expires_at - TOKEN_REFRESH_MARGIN > now):
        db.commit()
        return decrypt_secret(locked.access_token)

    try:
        token = connector.refresh_access_token(decrypt_secret(locked.refresh_token))
    except Exception as refresh_error:
        db.rollback()
        db.add(
            TokenRefreshAudit(
                id=uuid4(),
                store_id=locked.store_id,
                provider=connector.provider,
                success=False,
                error_message=str(refresh_error),
            )
        )
        db.commit()
        raise

    locked.access_token = encrypt_secret(token.access_token)
    if token.refresh_token:
        locked.refresh_token = encrypt_secret(token.refresh_token)
    locked.expires_at = token.expires_at
    db.add(TokenRefreshAudit(id=uuid4(), store_id=locked.store_id, provider=connector.provider, success=True))
    db.commit()
    return token.access_token


def _upsert_orders(db: Session, store_id: UUID, order_rows: list[dict]) -> None:
    """Upsert standardized order rows (connector output) into orders,
    resolving each one's customer — same steps as the Tiendanube webhook."""
    from app.models import orders as orders_table

    for order_data in order_rows:
        order_data = dict(order_data)
        order_data.pop("net_profit", None)  # generated column — Postgres computes this
        customer_email = order_data.pop("customer_email", None)
        customer_phone = order_data.pop("customer_phone", None)
        external_customer_id = order_data.pop("external_customer_id", None)
        order_data["time"] = _as_datetime(order_data["time"])
        order_data["customer_id"] = resolve_customer_id(
            db,
            store_id,
            customer_email,
            customer_phone,
            external_customer_id,
            order_time=order_data["time"],
        )
        stmt = pg_insert(orders_table).values([{"store_id": store_id, **order_data}])
        stmt = stmt.on_conflict_do_update(
            index_elements=["store_id", "order_id", "time"],
            set_={
                c.name: stmt.excluded[c.name]
                for c in orders_table.c
                if c.name not in ("time", "store_id", "order_id", "net_profit")
            },
        )
        db.execute(stmt)
    db.commit()


def _delete_orders(db: Session, store_id: UUID, order_ids: list[str]) -> int:
    """Remove orders that stopped being sales (cancelled, refunded)."""
    from app.models import orders as orders_table

    if not order_ids:
        return 0
    result = db.execute(
        orders_table.delete().where(orders_table.c.store_id == store_id, orders_table.c.order_id.in_(order_ids))
    )
    db.commit()
    return result.rowcount


def _as_datetime(value) -> datetime:
    """Connector rows carry the provider's ISO timestamp string."""
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))


# ============================================================================
# Sync-status reporting
# ============================================================================


@health_router.get("/health")
def connector_health(
    store: Store = Depends(get_owned_store),
    db: Session = Depends(get_db),
):
    """Per-provider connector status for this store (last sync/success/error).

    Backed by connector_status, which every OAuth callback, sync, and
    Shopify webhook above keeps updated.
    """
    rows = db.query(ConnectorStatus).filter(ConnectorStatus.store_id == store.id).all()
    return {
        row.provider: {
            "last_synced_at": row.last_synced_at,
            "last_success_at": row.last_success_at,
            "last_error": row.last_error,
        }
        for row in rows
    }
