"""Token handling: Meta's long-lived token, the shared refresh with a 'reconnect' state, and the Meta
Insights request shape."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from fastapi import status

from app.connectors import RECONNECT_MESSAGE, OAuthToken, ReconnectRequired
from app.connectors.meta import MetaConnector
from app.models import ConnectorStatus, StoreCredential, TokenRefreshAudit
from app.security import decrypt_secret, encrypt_secret
from app.services.connector_tokens import fresh_access_token, needs_reconnect


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        return self._body


class TestMetaLongLivedToken:
    def test_the_short_token_is_swapped_for_a_long_one_with_its_expiry(self, monkeypatch):
        calls = []

        def fake_get(url, params=None, **kwargs):
            calls.append(params)
            if params.get("grant_type") == "fb_exchange_token":
                return _Response({"access_token": "long-lived", "expires_in": 5184000})
            return _Response({"access_token": "short-lived", "expires_in": 7200})

        monkeypatch.setattr(requests, "get", fake_get)

        token = MetaConnector("s").exchange_auth_code("code", "https://x.test/cb")

        assert token.access_token == "long-lived"
        assert calls[1]["fb_exchange_token"] == "short-lived"
        remaining = token.expires_at - datetime.now(timezone.utc)
        assert timedelta(days=59) < remaining <= timedelta(days=60)

    def test_if_the_swap_fails_the_short_token_is_kept_with_its_own_expiry(self, monkeypatch):
        def fake_get(url, params=None, **kwargs):
            if params.get("grant_type") == "fb_exchange_token":
                raise requests.ConnectionError("down")
            return _Response({"access_token": "short-lived", "expires_in": 7200})

        monkeypatch.setattr(requests, "get", fake_get)

        token = MetaConnector("s").exchange_auth_code("code", "https://x.test/cb")

        assert token.access_token == "short-lived"
        assert token.expires_at - datetime.now(timezone.utc) < timedelta(hours=3)


class TestMetaInsightsRequest:
    def _fetch(self, monkeypatch, pages, **kwargs):
        seen = []

        def fake_get(url, params=None, headers=None, **_):
            seen.append({"url": url, "params": params, "headers": headers})
            return _Response(pages[len(seen) - 1])

        monkeypatch.setattr(requests, "get", fake_get)
        connector = MetaConnector("s", ad_account_id="act_1")
        records = connector.fetch_ad_spend(
            "tok", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 3, tzinfo=timezone.utc), **kwargs
        )
        return records, seen

    def _row(self, day, spend="10"):
        return {"campaign_id": "c1", "campaign_name": "C", "spend": spend, "date_start": day, "date_stop": day}

    def test_asks_for_daily_campaign_rows_and_not_for_an_invalid_breakdown(self, monkeypatch):
        _, seen = self._fetch(monkeypatch, [{"data": [self._row("2026-01-01")]}])

        params = seen[0]["params"]
        assert params["level"] == "campaign"
        assert params["time_increment"] == 1
        assert "breakdowns" not in params

    def test_the_token_is_in_a_header_and_never_in_the_url(self, monkeypatch):
        _, seen = self._fetch(monkeypatch, [{"data": []}])

        assert seen[0]["headers"] == {"Authorization": "Bearer tok"}
        assert "access_token" not in seen[0]["params"]

    def test_follows_the_next_link_and_stops_when_there_is_none(self, monkeypatch):
        pages = [
            {"data": [self._row("2026-01-01")], "paging": {"cursors": {"after": "a"}, "next": "https://graph.test/next"}},
            # the last page still has cursors: only the missing `next` says it is the last
            {"data": [self._row("2026-01-02")], "paging": {"cursors": {"after": "b"}}},
        ]

        records, seen = self._fetch(monkeypatch, pages)

        assert len(records) == 2
        assert [call["url"] for call in seen][1] == "https://graph.test/next"
        assert len(seen) == 2

    def test_rejects_an_unknown_level(self, monkeypatch):
        with pytest.raises(ValueError):
            self._fetch(monkeypatch, [{"data": []}], breakdown_by="breakdown")


class _Refresher:
    """A connector whose refresh does what the test says."""

    provider = "google"

    def __init__(self, token=None, error=None):
        self._token, self._error, self.calls = token, error, 0

    def refresh_access_token(self, refresh_token):
        self.calls += 1
        if self._error:
            raise self._error
        return self._token


def _credential(db, store, *, expires_in, refresh="refresh-1", provider="google"):
    credential = StoreCredential(
        id=uuid4(),
        store_id=store.id,
        provider=provider,
        access_token=encrypt_secret("old-access"),
        refresh_token=encrypt_secret(refresh) if refresh else None,
        expires_at=datetime.now(timezone.utc) + expires_in if expires_in is not None else None,
    )
    db.add(credential)
    db.commit()
    return credential


@pytest.mark.db
class TestFreshAccessToken:
    def test_a_token_far_from_expiry_is_used_as_is(self, test_db_session, test_store):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(hours=1))
        refresher = _Refresher()

        assert fresh_access_token(test_db_session, credential, refresher) == "old-access"
        assert refresher.calls == 0

    def test_an_expired_token_is_refreshed_and_a_rotated_refresh_token_is_kept(self, test_db_session, test_store):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(minutes=-1))
        new = OAuthToken("new-access", "refresh-2", datetime.now(timezone.utc) + timedelta(hours=1))

        token = fresh_access_token(test_db_session, credential, _Refresher(token=new))

        assert token == "new-access"
        test_db_session.refresh(credential)
        assert decrypt_secret(credential.access_token) == "new-access"
        assert decrypt_secret(credential.refresh_token) == "refresh-2"
        assert test_db_session.query(TokenRefreshAudit).filter_by(store_id=test_store.id, success=True).count() == 1

    def test_a_token_about_to_expire_is_refreshed_before_it_does(self, test_db_session, test_store):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(minutes=2))
        new = OAuthToken("new-access", None, datetime.now(timezone.utc) + timedelta(hours=1))

        assert fresh_access_token(test_db_session, credential, _Refresher(token=new)) == "new-access"

    def test_expired_with_no_refresh_token_asks_to_reconnect_and_says_so_in_the_status(
        self, test_db_session, test_store
    ):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(days=-1), refresh=None, provider="meta")
        connector = SimpleNamespace(provider="meta")

        with pytest.raises(ReconnectRequired):
            fresh_access_token(test_db_session, credential, connector)

        row = test_db_session.query(ConnectorStatus).filter_by(store_id=test_store.id, provider="meta").one()
        assert row.last_error == RECONNECT_MESSAGE

    def test_a_refresh_the_provider_rejects_asks_to_reconnect(self, test_db_session, test_store):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(minutes=-1))
        rejected = requests.HTTPError("400 invalid_grant", response=_Response({}, 400))

        with pytest.raises(ReconnectRequired):
            fresh_access_token(test_db_session, credential, _Refresher(error=rejected))

        audit = test_db_session.query(TokenRefreshAudit).filter_by(store_id=test_store.id, success=False).one()
        assert audit.error_message
        row = test_db_session.query(ConnectorStatus).filter_by(store_id=test_store.id, provider="google").one()
        assert row.last_error == RECONNECT_MESSAGE

    def test_a_network_failure_during_refresh_is_not_a_reason_to_reconnect(self, test_db_session, test_store):
        credential = _credential(test_db_session, test_store, expires_in=timedelta(minutes=-1))

        with pytest.raises(requests.ConnectionError):
            fresh_access_token(test_db_session, credential, _Refresher(error=requests.ConnectionError("down")))

        assert test_db_session.query(ConnectorStatus).filter_by(store_id=test_store.id).count() == 0


class TestNeedsReconnect:
    def test_expired_without_a_refresh_token(self):
        assert needs_reconnect(datetime.now(timezone.utc) - timedelta(minutes=1), False)

    def test_not_when_there_is_a_refresh_token_or_it_has_not_expired_or_never_expires(self):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        assert not needs_reconnect(past, True)
        assert not needs_reconnect(future, False)
        assert not needs_reconnect(None, False)


@pytest.mark.db
class TestHealthSaysWhenToReconnect:
    def test_flags_an_expired_token_with_no_refresh_token(self, client, auth_header, test_store, test_db_session):
        _credential(test_db_session, test_store, expires_in=timedelta(days=-2), refresh=None, provider="meta")

        response = client.get(f"/stores/{test_store.id}/connectors/health", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["meta"]["needs_reconnect"] is True

    def test_does_not_flag_a_healthy_connector(self, client, auth_header, test_store, test_db_session):
        _credential(test_db_session, test_store, expires_in=timedelta(days=30), refresh=None, provider="meta")

        response = client.get(f"/stores/{test_store.id}/connectors/health", headers=auth_header)

        assert response.json()["meta"]["needs_reconnect"] is False


class TestApiVersions:
    """The version each provider is asked for can be changed from the environment, so a provider
    retiring one does not need a code change. These run in a fresh interpreter because the version
    is read when the module is imported."""

    @staticmethod
    def _versions(env):
        import os
        import subprocess
        import sys
        from pathlib import Path

        code = (
            "from app.connectors.meta import MetaConnector as M\n"
            "from app.connectors.google import GoogleAdsConnector as G\n"
            "from app.connectors.linkedin import LinkedInConnector as L\n"
            "from app.connectors.shopify import ShopifyConnector as S\n"
            "print(M.API_VERSION, G.API_VERSION, L.API_VERSION, S.API_VERSION)\n"
        )
        backend = Path(__file__).resolve().parent.parent
        out = subprocess.run(
            [sys.executable, "-c", code], cwd=backend, capture_output=True, text=True, timeout=60,
            env={**os.environ, **env},
        )
        assert out.returncode == 0, out.stderr
        return out.stdout.split()

    def test_defaults_are_not_the_retired_versions(self):
        meta, google, linkedin, shopify = self._versions({})
        assert meta != "v19.0" and google != "v15" and linkedin != "202401" and shopify != "2024-01"

    def test_each_can_be_overridden_from_the_environment(self):
        versions = self._versions(
            {
                "META_API_VERSION": "v99.0",
                "GOOGLE_ADS_API_VERSION": "v99",
                "LINKEDIN_API_VERSION": "209901",
                "SHOPIFY_API_VERSION": "2099-01",
            }
        )
        assert versions == ["v99.0", "v99", "209901", "2099-01"]
