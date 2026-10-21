"""Tests for refresh token issuance, rotation, and revocation."""
import pytest
from fastapi import status


@pytest.mark.db
class TestRefreshTokenRotation:
    def test_refresh_with_valid_token_issues_new_pair(self, client, test_user):
        login = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        original_refresh = login.json()["refresh_token"]

        response = client.post("/auth/refresh", json={"refresh_token": original_refresh})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["access_token"]
        assert data["refresh_token"]
        assert data["refresh_token"] != original_refresh

    def test_reusing_rotated_token_is_rejected(self, client, test_user):
        login = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        original_refresh = login.json()["refresh_token"]

        first = client.post("/auth/refresh", json={"refresh_token": original_refresh})
        assert first.status_code == status.HTTP_200_OK

        second = client.post("/auth/refresh", json={"refresh_token": original_refresh})
        assert second.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unknown_token_is_rejected(self, client):
        response = client.post("/auth/refresh", json={"refresh_token": "not-a-real-token"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_rotated_token_still_works_for_new_access_token(self, client, test_user, test_store):
        login = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        refresh_response = client.post("/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
        new_access_token = refresh_response.json()["access_token"]

        me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {new_access_token}"})
        assert me_response.status_code == status.HTTP_200_OK
        assert me_response.json()["email"] == "testuser@example.com"


@pytest.mark.db
class TestLogout:
    def test_logout_revokes_token(self, client, test_user, auth_header):
        login = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        refresh_token = login.json()["refresh_token"]

        logout_response = client.post(
            "/auth/logout", headers=auth_header, json={"refresh_token": refresh_token},
        )
        assert logout_response.status_code == status.HTTP_204_NO_CONTENT

        refresh_attempt = client.post("/auth/refresh", json={"refresh_token": refresh_token})
        assert refresh_attempt.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_requires_auth(self, client, test_user):
        login = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        response = client.post("/auth/logout", json={"refresh_token": login.json()["refresh_token"]})
        # Matches the pre-existing HTTPBearer-with-no-header behavior
        # elsewhere in this codebase (see test_auth.py's
        # test_get_current_user_no_token) — FastAPI/Starlette return 401
        # here, not 403.
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_logout_on_someone_elses_token_404s(self, client, test_user, other_user, auth_header):
        other_login = client.post(
            "/auth/login",
            json={"email": "otheruser@example.com", "password": "otherpassword123"},
        )
        response = client.post(
            "/auth/logout", headers=auth_header, json={"refresh_token": other_login.json()["refresh_token"]},
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.db
class TestInviteAcceptIssuesRefreshToken:
    def test_accept_invite_returns_refresh_token(self, client, auth_header):
        create_response = client.post(
            "/accounts/invites",
            headers=auth_header,
            json={"email": "refresh-invitee@example.com", "role": "viewer"},
        )
        token = create_response.json()["token"]

        accept_response = client.post(
            "/accounts/invites/accept",
            json={ "accept_terms": True, "token": token, "password": "inviteepassword123"},
        )
        assert accept_response.status_code == status.HTTP_200_OK
        data = accept_response.json()
        assert data["access_token"]
        assert data["refresh_token"]


@pytest.mark.db
class TestMemberRemovalRevokesTokens:
    def test_removing_member_revokes_their_refresh_tokens(self, client, auth_header, admin_auth_header, admin_user):
        admin_login = client.post(
            "/auth/login",
            json={"email": "admin@example.com", "password": "adminpassword123"},
        )
        admin_refresh_token = admin_login.json()["refresh_token"]

        remove_response = client.delete(f"/accounts/members/{admin_user.id}", headers=auth_header)
        assert remove_response.status_code == status.HTTP_204_NO_CONTENT

        refresh_attempt = client.post("/auth/refresh", json={"refresh_token": admin_refresh_token})
        assert refresh_attempt.status_code == status.HTTP_401_UNAUTHORIZED
