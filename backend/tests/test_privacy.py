"""Consent when someone signs up, and the rights of the people who use ROSS: see their data, erase it.

The deletion tests matter most: an account's data lives in tables with and without a foreign key to its
stores, and "deleted" has to mean all of it is gone and nobody else's is touched.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import status
from sqlalchemy import text

from app.legal import LEGAL_VERSION
from app.models import Account, DemoRequest, Store, Subscription, User
from app.security import hash_password

PASSWORD = "testpassword123"

# Tables keyed by store_id without a foreign key: the ones a plain cascade would leave behind.
HYPERTABLES = ("orders", "pixel_events", "ad_spend", "creative_performance")


def _seed_store_data(db, store_id):
    now = datetime.now(timezone.utc)
    params = {"sid": store_id, "now": now}
    db.execute(
        text("INSERT INTO orders (time, store_id, order_id, gross_amount, currency) VALUES (:now, :sid, 'o-1', 10, 'USD')"),
        params,
    )
    db.execute(text("INSERT INTO pixel_events (time, store_id, event_name) VALUES (:now, :sid, 'page_view')"), params)
    db.execute(
        text("INSERT INTO ad_spend (time, store_id, platform, campaign_id, spend) VALUES (:now, :sid, 'meta', 'c1', 5)"),
        params,
    )
    db.execute(
        text(
            "INSERT INTO creative_performance (time, store_id, platform, campaign_id, ad_id, spend) "
            "VALUES (:now, :sid, 'meta', 'c1', 'ad-1', 5)"
        ),
        params,
    )
    db.commit()


def _count(db, table, store_id):
    return db.execute(text(f"SELECT count(*) FROM {table} WHERE store_id = :sid"), {"sid": store_id}).scalar()


def _delete_account(client, headers, **overrides):
    body = {"password": PASSWORD, "confirm": "ELIMINAR", **overrides}
    return client.post("/privacy/delete-account", headers=headers, json=body)


@pytest.mark.db
class TestConsentIsRequired:
    def test_registering_without_accepting_creates_nothing(self, client, test_db_session):
        response = client.post(
            "/auth/register",
            json={"account_name": "No Consent", "email": "noconsent@example.com", "password": "securepass123"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "términos" in response.json()["detail"]
        assert test_db_session.query(User).filter_by(email="noconsent@example.com").first() is None
        assert test_db_session.query(Account).filter_by(name="No Consent").first() is None

    def test_registering_records_what_was_accepted(self, client, test_db_session):
        response = client.post(
            "/auth/register",
            json={"account_name": "Consent Co", "email": "consent@example.com", "password": "securepass123", "accept_terms": True},
        )

        assert response.status_code == status.HTTP_201_CREATED
        user = test_db_session.query(User).filter_by(email="consent@example.com").one()
        assert user.terms_version == LEGAL_VERSION
        assert user.terms_accepted_at is not None

    def test_accepting_an_invite_needs_the_acceptance_too(self, client, test_user, auth_header, test_db_session):
        invite = client.post("/accounts/invites", headers=auth_header, json={"email": "guest@example.com", "role": "viewer"})
        token = invite.json()["token"]

        refused = client.post("/accounts/invites/accept", json={"token": token, "password": "guestpassword123"})
        accepted = client.post(
            "/accounts/invites/accept", json={"token": token, "password": "guestpassword123", "accept_terms": True}
        )

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert accepted.status_code == status.HTTP_200_OK
        guest = test_db_session.query(User).filter_by(email="guest@example.com").one()
        assert guest.terms_version == LEGAL_VERSION

    def test_a_demo_request_needs_the_privacy_acceptance(self, client, test_db_session):
        base = {"name": "Ana", "email": "ana@tienda.com", "platform": "shopify"}

        refused = client.post("/demo-requests", json=base)
        accepted = client.post("/demo-requests", json={**base, "accept_privacy": True})

        assert refused.status_code == status.HTTP_400_BAD_REQUEST
        assert accepted.status_code == status.HTTP_201_CREATED
        stored = test_db_session.query(DemoRequest).all()
        assert len(stored) == 1
        assert stored[0].privacy_version == LEGAL_VERSION
        assert stored[0].privacy_accepted_at is not None


@pytest.mark.db
class TestExportMyData:
    def test_returns_the_persons_own_data_as_a_download(self, client, auth_header, test_user, test_store):
        response = client.get("/privacy/export", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        assert "attachment" in response.headers["content-disposition"]
        data = response.json()
        assert data["user"]["email"] == test_user.email
        assert data["user"]["role"] == "owner"
        assert data["account_name"] == "Test Account"
        assert [m["store_name"] for m in data["store_memberships"]] == ["Test Store"]

    def test_never_includes_the_password_hash(self, client, auth_header, test_user):
        body = client.get("/privacy/export", headers=auth_header).text

        assert test_user.hashed_password not in body
        assert "hashed_password" not in body

    def test_does_not_include_other_accounts(self, client, auth_header, test_store, other_account, other_user, test_db_session):
        foreign = Store(id=uuid4(), account_id=other_account.id, name="Foreign Store", platform="shopify")
        test_db_session.add(foreign)
        test_db_session.commit()

        body = client.get("/privacy/export", headers=auth_header).text

        assert "Foreign Store" not in body
        assert "otheruser@example.com" not in body

    def test_requires_signing_in(self, client):
        assert client.get("/privacy/export").status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


@pytest.mark.db
class TestDeleteMyself:
    def test_a_member_can_delete_their_own_user(self, client, viewer_user, viewer_auth_header, test_db_session):
        response = client.post("/privacy/delete-me", headers=viewer_auth_header, json={"password": "viewerpassword123"})

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert test_db_session.query(User).filter_by(email="viewer@example.com").first() is None
        login = client.post("/auth/login", json={"email": "viewer@example.com", "password": "viewerpassword123"})
        assert login.status_code == status.HTTP_401_UNAUTHORIZED

    def test_the_account_and_the_others_stay(self, client, test_user, viewer_user, viewer_auth_header, test_store, test_db_session):
        client.post("/privacy/delete-me", headers=viewer_auth_header, json={"password": "viewerpassword123"})

        assert test_db_session.query(User).filter_by(email="testuser@example.com").first() is not None
        assert test_db_session.get(Store, test_store.id) is not None

    def test_needs_the_right_password(self, client, viewer_user, viewer_auth_header, test_db_session):
        response = client.post("/privacy/delete-me", headers=viewer_auth_header, json={"password": "wrong-password"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert test_db_session.query(User).filter_by(email="viewer@example.com").first() is not None

    def test_the_only_owner_cannot_leave_the_account_without_anyone(self, client, test_user, auth_header, test_db_session):
        response = client.post("/privacy/delete-me", headers=auth_header, json={"password": PASSWORD})

        assert response.status_code == status.HTTP_409_CONFLICT
        assert test_db_session.query(User).filter_by(email="testuser@example.com").first() is not None

    def test_an_owner_can_leave_when_another_owner_remains(self, client, test_user, test_account, auth_header, test_db_session):
        test_db_session.add(
            User(id=uuid4(), account_id=test_account.id, email="coowner@example.com", hashed_password=hash_password("x" * 12), role="owner")
        )
        test_db_session.commit()

        response = client.post("/privacy/delete-me", headers=auth_header, json={"password": PASSWORD})

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert test_db_session.query(User).filter_by(email="testuser@example.com").first() is None


@pytest.mark.db
class TestDeleteAccount:
    def test_removes_the_account_its_people_and_every_piece_of_data(
        self, client, auth_header, test_account, test_user, viewer_user, test_store, test_db_session
    ):
        _seed_store_data(test_db_session, test_store.id)
        for table in HYPERTABLES:
            assert _count(test_db_session, table, test_store.id) == 1

        response = _delete_account(client, auth_header)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        test_db_session.expire_all()
        assert test_db_session.get(Account, test_account.id) is None
        assert test_db_session.query(User).filter(User.account_id == test_account.id).count() == 0
        assert test_db_session.get(Store, test_store.id) is None
        for table in HYPERTABLES:
            assert _count(test_db_session, table, test_store.id) == 0, f"{table} kept rows of a deleted account"

    def test_leaves_every_other_account_untouched(
        self, client, auth_header, test_store, other_account, other_user, test_db_session
    ):
        other_store = Store(id=uuid4(), account_id=other_account.id, name="Other Store", platform="shopify")
        test_db_session.add(other_store)
        test_db_session.commit()
        _seed_store_data(test_db_session, other_store.id)
        _seed_store_data(test_db_session, test_store.id)

        _delete_account(client, auth_header)

        test_db_session.expire_all()
        assert test_db_session.get(Account, other_account.id) is not None
        assert test_db_session.get(Store, other_store.id) is not None
        for table in HYPERTABLES:
            assert _count(test_db_session, table, other_store.id) == 1, f"{table} lost another account's rows"

    def test_cannot_log_in_afterwards(self, client, auth_header, test_user):
        _delete_account(client, auth_header)

        login = client.post("/auth/login", json={"email": "testuser@example.com", "password": PASSWORD})
        assert login.status_code == status.HTTP_401_UNAUTHORIZED

    def test_needs_the_right_password(self, client, auth_header, test_account, test_db_session):
        response = _delete_account(client, auth_header, password="wrong-password")

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert test_db_session.get(Account, test_account.id) is not None

    def test_needs_the_confirmation_word_typed_in_full(self, client, auth_header, test_account, test_db_session):
        for confirm in ("eliminar", "ELIMIN", "", "SI"):
            response = _delete_account(client, auth_header, confirm=confirm)
            assert response.status_code == 422, confirm

        assert test_db_session.get(Account, test_account.id) is not None

    @pytest.mark.parametrize("role", ["admin", "viewer"])
    def test_only_an_owner_can_delete_the_account(self, client, test_account, test_db_session, role):
        member = User(id=uuid4(), account_id=test_account.id, email=f"{role}-x@example.com", hashed_password=hash_password("memberpass123"), role=role)
        test_db_session.add(member)
        test_db_session.commit()
        login = client.post("/auth/login", json={"email": member.email, "password": "memberpass123"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post("/privacy/delete-account", headers=headers, json={"password": "memberpass123", "confirm": "ELIMINAR"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert test_db_session.get(Account, test_account.id) is not None

    def test_a_live_paid_subscription_blocks_it_and_nothing_is_deleted(
        self, client, auth_header, test_account, test_store, test_db_session
    ):
        test_db_session.add(
            Subscription(
                account_id=test_account.id,
                plan_id="growth",
                status="active",
                payment_provider="mercadopago",
                provider_subscription_id="preapproval-123",
            )
        )
        test_db_session.commit()
        _seed_store_data(test_db_session, test_store.id)

        response = _delete_account(client, auth_header)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert "suscripción" in response.json()["detail"]
        assert test_db_session.get(Account, test_account.id) is not None
        assert _count(test_db_session, "orders", test_store.id) == 1

    def test_a_cancelled_subscription_does_not_block_it(self, client, auth_header, test_account, test_db_session):
        test_db_session.add(
            Subscription(
                account_id=test_account.id,
                plan_id="growth",
                status="canceled",
                payment_provider="mercadopago",
                provider_subscription_id="preapproval-123",
            )
        )
        test_db_session.commit()

        assert _delete_account(client, auth_header).status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.db
def test_every_table_of_an_account_goes_with_it(test_db_engine):
    """A table added with an account_id and no ON DELETE CASCADE to accounts would survive a deleted account."""
    with test_db_engine.connect() as connection:
        missing = connection.execute(
            text(
                """
                SELECT c.table_name
                FROM information_schema.columns c
                WHERE c.table_schema = current_schema() AND c.column_name = 'account_id'
                  AND NOT EXISTS (
                    SELECT 1
                    FROM information_schema.key_column_usage k
                    JOIN information_schema.referential_constraints r
                      ON r.constraint_name = k.constraint_name AND r.constraint_schema = k.constraint_schema
                    WHERE k.table_schema = c.table_schema AND k.table_name = c.table_name
                      AND k.column_name = 'account_id' AND r.delete_rule = 'CASCADE'
                  )
                """
            )
        ).scalars().all()

    assert not missing, f"Tables with an account_id and no ON DELETE CASCADE to accounts: {missing}"
