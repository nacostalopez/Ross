"""The public read-only demo: refresh_demo_account, POST /auth/demo, and the
read-only guard in get_current_user."""

import contextlib
import random
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import status
from sqlalchemy import func, select

from app.cli import refresh_demo_account as cli
from app.config import settings
from app.models import Account, Customer, Store, User, ad_spend, creative_performance, orders
from app.services.demo_account import refresh_demo_account

DEMO_EMAIL = "demo-viewer@ross.test"
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def demo_enabled(monkeypatch):
    monkeypatch.setattr(settings, "demo_viewer_email", DEMO_EMAIL)


def _count(db, table, store_id):
    return db.execute(select(func.count()).select_from(table).where(table.c.store_id == store_id)).scalar()


@pytest.mark.db
class TestRefreshDemoAccount:
    def test_creates_a_viewer_and_a_store_with_90_days_of_data(self, test_db_session):
        store = refresh_demo_account(test_db_session, DEMO_EMAIL, now=NOW, rng=random.Random(1))

        viewer = test_db_session.query(User).filter_by(email=DEMO_EMAIL).one()
        assert viewer.role == "viewer"
        assert store.account_id == viewer.account_id
        assert store.currency == "USD"
        assert _count(test_db_session, orders, store.id) > 90 * 3
        assert _count(test_db_session, ad_spend, store.id) == 90 * 4
        assert _count(test_db_session, creative_performance, store.id) == 14 * 8
        newest, oldest = test_db_session.execute(
            select(func.max(orders.c.time), func.min(orders.c.time)).where(orders.c.store_id == store.id)
        ).one()
        assert newest <= NOW
        assert oldest >= NOW - timedelta(days=90)
        assert test_db_session.query(Customer).filter_by(store_id=store.id).count() == 6

    def test_a_second_refresh_replaces_the_data_instead_of_adding(self, test_db_session):
        first = refresh_demo_account(test_db_session, DEMO_EMAIL, now=NOW, rng=random.Random(1))
        later = NOW + timedelta(days=10)
        second = refresh_demo_account(test_db_session, DEMO_EMAIL, now=later, rng=random.Random(1))

        assert second.id == first.id
        assert test_db_session.query(Account).filter(Account.id == first.account_id).count() == 1
        assert test_db_session.query(Store).filter_by(account_id=first.account_id).count() == 1
        assert _count(test_db_session, ad_spend, second.id) == 90 * 4
        oldest = test_db_session.execute(select(func.min(orders.c.time)).where(orders.c.store_id == second.id)).scalar()
        assert oldest >= later - timedelta(days=90)
        assert test_db_session.query(Customer).filter_by(store_id=second.id).count() == 6

    def test_refuses_to_turn_an_existing_non_viewer_into_the_demo(self, test_db_session, test_user):
        with pytest.raises(ValueError):
            refresh_demo_account(test_db_session, test_user.email, now=NOW)


@pytest.mark.db
class TestDemoSession:
    def test_404_when_no_demo_is_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "demo_viewer_email", "")
        assert client.post("/auth/demo").status_code == status.HTTP_404_NOT_FOUND

    def test_404_when_configured_but_not_created_yet(self, client, demo_enabled):
        assert client.post("/auth/demo").status_code == status.HTTP_404_NOT_FOUND

    def test_never_hands_out_a_non_viewer(self, client, monkeypatch, test_user):
        monkeypatch.setattr(settings, "demo_viewer_email", test_user.email)
        assert client.post("/auth/demo").status_code == status.HTTP_404_NOT_FOUND

    def test_session_reads_the_demo_store(self, client, test_db_session, demo_enabled):
        store = refresh_demo_account(test_db_session, DEMO_EMAIL, now=datetime.now(timezone.utc), rng=random.Random(2))

        response = client.post("/auth/demo")

        assert response.status_code == status.HTTP_200_OK
        headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        stores = client.get("/stores", headers=headers).json()
        assert [s["id"] for s in stores] == [str(store.id)]
        assert stores[0]["effective_role"] == "viewer"
        end = datetime.now(timezone.utc)
        summary = client.get(
            f"/stores/{store.id}/metrics/summary",
            headers=headers,
            params={"start": (end - timedelta(days=30)).isoformat(), "end": end.isoformat()},
        )
        assert summary.status_code == status.HTTP_200_OK
        assert summary.json()["true_roas"] is not None


@pytest.mark.db
class TestDemoIsReadOnly:
    @pytest.fixture
    def demo_headers(self, client, test_db_session, demo_enabled):
        refresh_demo_account(test_db_session, DEMO_EMAIL, now=datetime.now(timezone.utc), rng=random.Random(3))
        token = client.post("/auth/demo").json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.parametrize(
        "method, path, body",
        [
            ("put", "/dashboard/layout", {"widgets": []}),
            ("post", "/push/subscribe", {"endpoint": "https://push.example.com/x", "keys": {"p256dh": "a", "auth": "b"}}),
            ("post", "/stores", {"name": "Otra", "platform": "shopify", "currency": "USD"}),
        ],
    )
    def test_writes_are_refused(self, client, demo_headers, method, path, body):
        response = getattr(client, method)(path, headers=demo_headers, json=body)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "solo lectura" in response.json()["detail"]

    def test_a_regular_viewer_is_not_affected(self, client, demo_enabled, viewer_auth_header):
        # The same (valid) request a regular viewer can make about themselves still works.
        response = client.put("/dashboard/layout", headers=viewer_auth_header, json={"widgets": []})
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.db
class TestRefreshCommand:
    def test_disabled_without_a_demo_email(self, monkeypatch, capsys):
        monkeypatch.setattr(settings, "demo_viewer_email", "")
        assert cli.main() == 1
        assert "DEMO_VIEWER_EMAIL" in capsys.readouterr().out

    def test_refreshes_and_reports_the_store(self, monkeypatch, capsys, test_db_session, demo_enabled):
        # The command closes its session before printing; expunge_all() makes the test session
        # behave the same, so reading anything off a detached object would fail here too.
        def session():
            @contextlib.contextmanager
            def scope():
                yield test_db_session
                test_db_session.expunge_all()
            return scope()

        monkeypatch.setattr(cli, "SessionLocal", session)

        assert cli.main() == 0
        store = test_db_session.query(Store).join(User, User.account_id == Store.account_id).filter(User.email == DEMO_EMAIL).one()
        assert f"store {store.id}" in capsys.readouterr().out
