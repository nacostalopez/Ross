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
from app.connectors.mercadopago import MercadoPagoConnector
from app.connectors.meta import MetaConnector
from app.connectors.shopify import ShopifyConnector
from app.connectors.tiendanube import TiendanubeConnector
from app.connectors.tiktok import TikTokConnector
from app.database import get_db
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

logger = logging.getLogger("escal.connectors")

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


@router.post("/mercadopago/sync-ad-spend")
def sync_mercadopago_ad_spend(
    start_date: datetime = Query(...),
    end_date: datetime = Query(...),
    store: Store = Depends(get_owned_store),
    _: User = Depends(require_store_role("owner", "admin")),
    db: Session = Depends(get_db),
):
    """Sync ad spend data from MercadoPago."""
    store_id = store.id

    # Get MercadoPago credentials
    credential = (
        db.query(StoreCredential)
        .filter_by(
            store_id=store_id,
            provider="mercadopago",
        )
        .first()
    )

    if not credential:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MercadoPago credentials not configured for this store",
        )

    try:
        access_token = decrypt_secret(credential.access_token)

        # Check if token is expired and refresh if needed
        if credential.expires_at and datetime.now(timezone.utc) > credential.expires_at:
            refresh_token = decrypt_secret(credential.refresh_token) if credential.refresh_token else None
            if refresh_token:
                try:
                    connector = MercadoPagoConnector(str(store_id), credential.provider_account_id)
                    new_token = connector.refresh_access_token(refresh_token)
                    access_token = new_token.access_token
                    credential.access_token = encrypt_secret(access_token)
                    credential.refresh_token = (
                        encrypt_secret(new_token.refresh_token) if new_token.refresh_token else None
                    )
                    credential.expires_at = new_token.expires_at
                    db.commit()
                    db.add(TokenRefreshAudit(id=uuid4(), store_id=store_id, provider="mercadopago", success=True))
                    db.commit()
                except Exception as refresh_error:
                    db.add(
                        TokenRefreshAudit(
                            id=uuid4(),
                            store_id=store_id,
                            provider="mercadopago",
                            success=False,
                            error_message=str(refresh_error),
                        )
                    )
                    db.commit()
                    raise

        connector = MercadoPagoConnector(str(store_id), credential.provider_account_id)
        spend_records = connector.fetch_ad_spend(access_token, start_date, end_date)

        # Ingest into ad_spend table
        from app.models import ad_spend as ad_spend_table

        rows = [{"store_id": store_id, **record} for record in spend_records]
        if rows:
            db.execute(insert(ad_spend_table), rows)
            db.commit()

        _upsert_connector_status(db, store_id, "mercadopago", synced=True, success=True)

        return {
            "status": "success",
            "records_synced": len(rows),
        }
    except Exception as e:
        _upsert_connector_status(db, store_id, "mercadopago", synced=True, success=False, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to sync MercadoPago ad spend: {str(e)}",
        )


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
