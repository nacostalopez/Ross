"""Financial integrity tests for idempotent ad-spend synchronization."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from app.connectors.google import GoogleAdsConnector
from app.connectors.linkedin import LinkedInConnector
from app.connectors.meta import MetaConnector
from app.connectors.tiktok import TikTokConnector
from app.models import StoreCredential
from app.models import ad_spend as ad_spend_table
from app.security import encrypt_secret

PROVIDERS = [
    ("meta", MetaConnector),
    ("google", GoogleAdsConnector),
    ("tiktok", TikTokConnector),
    ("linkedin", LinkedInConnector),
]


@pytest.mark.db
@pytest.mark.parametrize(("provider", "connector_class"), PROVIDERS)
def test_ad_spend_resync_replaces_only_matching_store_platform_and_date(
    client, auth_header, test_store, test_db_session, monkeypatch, provider, connector_class
):
    day = datetime(2026, 1, 10, tzinfo=timezone.utc)
    test_db_session.add(
        StoreCredential(
            id=uuid4(),
            store_id=test_store.id,
            provider=provider,
            access_token=encrypt_secret("test-access-token"),
            provider_account_id="account-123",
        )
    )
    test_db_session.execute(
        insert(ad_spend_table),
        [
            {
                "time": day,
                "store_id": test_store.id,
                "platform": provider,
                "campaign_id": "campaign-1",
                "campaign_name": "Old value",
                "adset_id": "",
                "spend": 3,
                "impressions": 10,
                "clicks": 1,
            },
            {
                "time": day,
                "store_id": test_store.id,
                "platform": "unrelated-platform",
                "campaign_id": "campaign-2",
                "campaign_name": "Keep this",
                "adset_id": "",
                "spend": 4,
                "impressions": 20,
                "clicks": 2,
            },
            {
                "time": datetime(2026, 1, 11, tzinfo=timezone.utc),
                "store_id": test_store.id,
                "platform": provider,
                "campaign_id": "campaign-3",
                "campaign_name": "Outside range",
                "adset_id": "",
                "spend": 6,
                "impressions": 30,
                "clicks": 3,
            },
        ],
    )
    test_db_session.commit()

    fetched_records = [
        {
            "time": day,
            "platform": provider,
            "campaign_id": "campaign-1",
            "campaign_name": "Revised value",
            "adset_id": "",
            "spend": 9.25,
            "impressions": 25,
            "clicks": 5,
        }
    ]
    monkeypatch.setattr(connector_class, "fetch_ad_spend", lambda *_args, **_kwargs: fetched_records)

    params = {
        "store_id": str(test_store.id),
        "start_date": "2026-01-10T00:00:00Z",
        "end_date": "2026-01-10T00:00:00Z",
    }
    endpoint = f"/connectors/{provider}/sync-ad-spend"
    first = client.post(endpoint, headers=auth_header, params=params)
    second = client.post(endpoint, headers=auth_header, params=params)

    assert first.status_code == second.status_code == 200
    assert first.json()["records_synced"] == second.json()["records_synced"] == 1

    rows = test_db_session.execute(
        select(ad_spend_table).where(ad_spend_table.c.store_id == test_store.id)
    ).mappings().all()
    provider_rows = [row for row in rows if row["platform"] == provider]
    other_platform_rows = [row for row in rows if row["platform"] == "unrelated-platform"]

    assert len(provider_rows) == 2
    assert sum(float(row["spend"]) for row in provider_rows) == 15.25
    assert len([row for row in provider_rows if row["campaign_name"] == "Revised value"]) == 1
    assert len(other_platform_rows) == 1
    assert float(other_platform_rows[0]["spend"]) == 4

    monkeypatch.setattr(connector_class, "fetch_ad_spend", lambda *_args, **_kwargs: [])
    empty_sync = client.post(endpoint, headers=auth_header, params=params)
    assert empty_sync.status_code == 200
    assert empty_sync.json()["records_synced"] == 0

    remaining_provider_rows = test_db_session.execute(
        select(ad_spend_table).where(
            ad_spend_table.c.store_id == test_store.id,
            ad_spend_table.c.platform == provider,
        )
    ).mappings().all()
    assert len(remaining_provider_rows) == 1
    assert remaining_provider_rows[0]["time"].date().isoformat() == "2026-01-11"
