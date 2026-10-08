"""Tests for the Owner/Admin/Viewer role-based permission system.

Follows the same recipe as test_ownership.py: hit a real endpoint as a given
role's auth header and assert the expected status code. Ownership (cross
-account access) is covered separately in test_ownership.py — these tests
are all same-account, different-role.
"""

from uuid import uuid4

import pytest
from fastapi import status


@pytest.mark.db
class TestViewerBlockedFromWrites:
    def test_viewer_cannot_create_store(self, client, viewer_auth_header):
        response = client.post(
            "/stores",
            headers=viewer_auth_header,
            json={"name": "New Store", "platform": "shopify", "currency": "USD", "timezone": "UTC"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_upsert_credentials(self, client, viewer_auth_header, test_store):
        response = client.put(
            f"/stores/{test_store.id}/credentials",
            headers=viewer_auth_header,
            json={"provider": "shopify", "access_token": "tok"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_upsert_products(self, client, viewer_auth_header, test_store):
        response = client.put(
            f"/stores/{test_store.id}/products",
            headers=viewer_auth_header,
            json=[{"external_id": "p1", "title": "Product 1"}],
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_ingest_orders(self, client, viewer_auth_header, test_store):
        response = client.post(
            f"/stores/{test_store.id}/orders",
            headers=viewer_auth_header,
            json=[{"order_id": "o1", "time": "2026-01-01T00:00:00Z", "gross_amount": 10.0, "currency": "USD"}],
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_ingest_pixel_events(self, client, viewer_auth_header, test_store):
        response = client.post(
            f"/stores/{test_store.id}/pixel-events",
            headers=viewer_auth_header,
            json=[{"event_name": "view", "time": "2026-01-01T00:00:00Z"}],
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_ingest_ad_spend(self, client, viewer_auth_header, test_store):
        response = client.post(
            f"/stores/{test_store.id}/ad-spend",
            headers=viewer_auth_header,
            json=[{"time": "2026-01-01T00:00:00Z", "platform": "meta", "campaign_id": "c1", "spend": 1.0}],
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_viewer_cannot_get_connector_auth_url(self, client, viewer_auth_header, test_store):
        response = client.post(
            "/connectors/shopify/auth-url",
            headers=viewer_auth_header,
            params={"store_id": str(test_store.id), "shop_domain": "x.myshopify.com"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.db
class TestViewerCanRead:
    def test_viewer_can_list_stores(self, client, viewer_auth_header):
        response = client.get("/stores", headers=viewer_auth_header)
        assert response.status_code == status.HTTP_200_OK

    def test_viewer_can_get_store(self, client, viewer_auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}", headers=viewer_auth_header)
        assert response.status_code == status.HTTP_200_OK

    def test_viewer_can_list_orders(self, client, viewer_auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/orders", headers=viewer_auth_header)
        assert response.status_code == status.HTTP_200_OK

    def test_viewer_can_view_connector_health(self, client, viewer_auth_header, test_store):
        response = client.get(f"/stores/{test_store.id}/connectors/health", headers=viewer_auth_header)
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.db
class TestAdminCanWriteButNotManageUsers:
    def test_admin_can_create_store(self, client, admin_auth_header):
        response = client.post(
            "/stores",
            headers=admin_auth_header,
            json={"name": "Admin Store", "platform": "shopify", "currency": "USD", "timezone": "UTC"},
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_admin_can_ingest_orders(self, client, admin_auth_header, test_store):
        response = client.post(
            f"/stores/{test_store.id}/orders",
            headers=admin_auth_header,
            json=[{"order_id": "o1", "time": "2026-01-01T00:00:00Z", "gross_amount": 10.0, "currency": "USD"}],
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_admin_can_get_connector_auth_url(self, client, admin_auth_header, test_store):
        response = client.post(
            "/connectors/shopify/auth-url",
            headers=admin_auth_header,
            params={"store_id": str(test_store.id), "shop_domain": "x.myshopify.com"},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_admin_cannot_create_invite(self, client, admin_auth_header):
        response = client.post(
            "/accounts/invites",
            headers=admin_auth_header,
            json={"email": "newperson@example.com", "role": "viewer"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_cannot_change_member_role(self, client, admin_auth_header, viewer_user):
        response = client.patch(
            f"/accounts/members/{viewer_user.id}/role",
            headers=admin_auth_header,
            json={"role": "admin"},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_cannot_remove_member(self, client, admin_auth_header, viewer_user):
        response = client.delete(f"/accounts/members/{viewer_user.id}", headers=admin_auth_header)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_list_members(self, client, admin_auth_header):
        response = client.get("/accounts/members", headers=admin_auth_header)
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.db
class TestOwnerFullAccess:
    def test_owner_can_create_store(self, client, auth_header):
        response = client.post(
            "/stores",
            headers=auth_header,
            json={"name": "Owner Store", "platform": "shopify", "currency": "USD", "timezone": "UTC"},
        )
        assert response.status_code == status.HTTP_201_CREATED

    def test_owner_can_create_invite(self, client, auth_header):
        response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "newperson@example.com", "role": "viewer"},
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.json()["token"]

    def test_owner_can_list_members(self, client, auth_header, admin_user, viewer_user):
        response = client.get("/accounts/members", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        emails = {m["email"] for m in response.json()}
        assert {"testuser@example.com", "admin@example.com", "viewer@example.com"}.issubset(emails)


@pytest.mark.db
class TestInviteFlow:
    def test_invite_create_accept_and_login(self, client, auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "invitee@example.com", "role": "viewer"},
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        token = create_response.json()["token"]

        accept_response = client.post(
            "/accounts/invites/accept",
            json={"token": token, "password": "inviteepassword123"},
        )
        assert accept_response.status_code == status.HTTP_200_OK
        access_token = accept_response.json()["access_token"]

        me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {access_token}"})
        assert me_response.status_code == status.HTTP_200_OK
        assert me_response.json()["role"] == "viewer"

    def test_invite_duplicate_email_conflicts(self, client, auth_header, test_user):
        response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": test_user.email, "role": "viewer"},
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_invite_to_an_existing_email_written_differently_conflicts(self, client, auth_header, test_user):
        response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": test_user.email.upper(), "role": "viewer"},
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_second_pending_invite_written_differently_conflicts(self, client, auth_header):
        first = client.post(
            "/accounts/invites", headers=auth_header, json={"email": "invitee@example.com", "role": "viewer"}
        )
        assert first.status_code == status.HTTP_201_CREATED

        second = client.post(
            "/accounts/invites", headers=auth_header, json={"email": "Invitee@Example.com", "role": "admin"}
        )
        assert second.status_code == status.HTTP_409_CONFLICT

    def test_accepting_after_the_email_registered_on_its_own_conflicts(self, client, auth_header):
        invite = client.post(
            "/accounts/invites", headers=auth_header, json={"email": "invitee@example.com", "role": "viewer"}
        )
        registered = client.post(
            "/auth/register",
            json={"account_name": "Propia", "email": "INVITEE@example.com", "password": "securepass123"},
        )
        assert registered.status_code == status.HTTP_201_CREATED

        response = client.post(
            "/accounts/invites/accept",
            json={"token": invite.json()["token"], "password": "inviteepassword123"},
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_accept_invalid_token_rejected(self, client):
        response = client.post(
            "/accounts/invites/accept",
            json={"token": "not-a-real-token", "password": "somepassword123"},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_revoked_invite_cannot_be_accepted(self, client, auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "revoked@example.com", "role": "viewer"},
        )
        invite_id = create_response.json()["id"]
        token = create_response.json()["token"]

        revoke_response = client.delete(f"/accounts/invites/{invite_id}", headers=auth_header)
        assert revoke_response.status_code == status.HTTP_204_NO_CONTENT

        accept_response = client.post(
            "/accounts/invites/accept",
            json={"token": token, "password": "somepassword123"},
        )
        assert accept_response.status_code == status.HTTP_400_BAD_REQUEST

    def test_resend_issues_a_new_token_that_works_and_invalidates_the_old_one(self, client, auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "resend-me@example.com", "role": "viewer"},
        )
        invite_id = create_response.json()["id"]
        original_token = create_response.json()["token"]

        resend_response = client.post(f"/accounts/invites/{invite_id}/resend", headers=auth_header)
        assert resend_response.status_code == status.HTTP_200_OK
        new_token = resend_response.json()["token"]
        assert new_token != original_token

        old_token_attempt = client.post(
            "/accounts/invites/accept",
            json={"token": original_token, "password": "somepassword123"},
        )
        assert old_token_attempt.status_code == status.HTTP_400_BAD_REQUEST

        new_token_attempt = client.post(
            "/accounts/invites/accept",
            json={"token": new_token, "password": "somepassword123"},
        )
        assert new_token_attempt.status_code == status.HTTP_200_OK

    def test_admin_cannot_resend_invite(self, client, auth_header, admin_auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "admin-cant-resend@example.com", "role": "viewer"},
        )
        invite_id = create_response.json()["id"]

        response = client.post(f"/accounts/invites/{invite_id}/resend", headers=admin_auth_header)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_resend_revoked_invite_rejected(self, client, auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "revoked-resend@example.com", "role": "viewer"},
        )
        invite_id = create_response.json()["id"]
        client.delete(f"/accounts/invites/{invite_id}", headers=auth_header)

        response = client.post(f"/accounts/invites/{invite_id}/resend", headers=auth_header)
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_resend_unknown_invite_404s(self, client, auth_header):
        response = client.post(
            "/accounts/invites/00000000-0000-0000-0000-000000000000/resend",
            headers=auth_header,
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.db
class TestLastOwnerGuard:
    def test_sole_owner_cannot_demote_self(self, client, auth_header, test_user):
        response = client.patch(
            f"/accounts/members/{test_user.id}/role",
            headers=auth_header,
            json={"role": "admin"},
        )
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_sole_owner_cannot_remove_self(self, client, auth_header, test_user):
        response = client.delete(f"/accounts/members/{test_user.id}", headers=auth_header)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_owner_can_remove_an_admin(self, client, auth_header, admin_auth_header, admin_user):
        response = client.delete(f"/accounts/members/{admin_user.id}", headers=auth_header)
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # The removed admin's row is gone — a fresh login attempt fails.
        login_response = client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "adminpassword123"},
        )
        assert login_response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_member_not_in_account_returns_404(self, client, auth_header, other_user):
        response = client.delete(f"/accounts/members/{other_user.id}", headers=auth_header)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unknown_member_returns_404(self, client, auth_header):
        response = client.delete(f"/accounts/members/{uuid4()}", headers=auth_header)
        assert response.status_code == status.HTTP_404_NOT_FOUND
