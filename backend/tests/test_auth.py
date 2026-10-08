"""Tests for authentication endpoints and security."""
import pytest
from fastapi import status


@pytest.mark.db
class TestRegister:
    """Test user registration."""

    def test_register_success(self, client):
        """Test successful registration creates account and user."""
        response = client.post(
            "/auth/register",
            json={
                "account_name": "New Account",
                "email": "newuser@example.com",
                "password": "securepass123",
            },
        )
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_register_duplicate_email(self, client, test_user):
        """Test registration with already-registered email fails."""
        response = client.post(
            "/auth/register",
            json={
                "account_name": "Another Account",
                "email": "testuser@example.com",
                "password": "securepass123",
            },
        )
        
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "Ya hay una cuenta con ese email" in response.json()["detail"]

    @pytest.mark.parametrize("email", ["TestUser@example.com", "testuser@EXAMPLE.COM", "  testuser@example.com "])
    def test_register_same_email_written_differently_is_a_duplicate(self, client, test_user, email):
        """A phone capitalising the first letter used to open a second account for the same person."""
        response = client.post(
            "/auth/register",
            json={"account_name": "Otra cuenta", "email": email, "password": "securepass123"},
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_register_stores_the_email_lowercased(self, client, test_db_session):
        response = client.post(
            "/auth/register",
            json={"account_name": "Nueva", "email": " Ana.Perez@Example.com ", "password": "securepass123"},
        )

        assert response.status_code == status.HTTP_201_CREATED
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {response.json()['access_token']}"})
        assert me.json()["email"] == "ana.perez@example.com"

    def test_register_rejects_an_empty_account_name(self, client):
        response = client.post(
            "/auth/register",
            json={"account_name": "", "email": "someone@example.com", "password": "securepass123"},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_database_rejects_a_case_variant_of_an_existing_email(self, test_db_session, test_user):
        """The unique index on lower(email) holds even if two sign-ups race past the app check."""
        from uuid import uuid4

        from sqlalchemy.exc import IntegrityError

        from app.models import User

        test_db_session.add(
            User(id=uuid4(), account_id=test_user.account_id, email="TESTUSER@example.com", hashed_password="x", role="viewer")
        )
        with pytest.raises(IntegrityError):
            test_db_session.flush()
        test_db_session.rollback()

    def test_register_invalid_email(self, client):
        """Test registration with invalid email fails."""
        response = client.post(
            "/auth/register",
            json={
                "account_name": "New Account",
                "email": "not-an-email",
                "password": "securepass123",
            },
        )
        
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.db
class TestLogin:
    """Test user login."""

    def test_login_success(self, client, test_user):
        """Test successful login returns access token."""
        response = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "testpassword123"},
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client, test_user):
        """Test login with wrong password fails."""
        response = client.post(
            "/auth/login",
            json={"email": "testuser@example.com", "password": "wrongpassword"},
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["detail"] == "Email o contraseña incorrectos"

    def test_login_ignores_case_and_spaces_in_the_email(self, client, test_user):
        response = client.post(
            "/auth/login",
            json={"email": " TestUser@Example.com ", "password": "testpassword123"},
        )

        assert response.status_code == status.HTTP_200_OK

    def test_login_nonexistent_user(self, client):
        """Test login with nonexistent email fails."""
        response = client.post(
            "/auth/login",
            json={"email": "nonexistent@example.com", "password": "anypassword"},
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()["detail"] == "Email o contraseña incorrectos"


@pytest.mark.db
class TestAuthenticatedRequests:
    """Test authenticated endpoints."""

    def test_get_current_user_success(self, client, auth_header):
        """Test /auth/me returns current user."""
        response = client.get("/auth/me", headers=auth_header)
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["email"] == "testuser@example.com"

    def test_get_current_user_no_token(self, client):
        """Test /auth/me without token fails."""
        response = client.get("/auth/me")

        # FastAPI's HTTPBearer (auto_error=True) raises 401 for a missing
        # Authorization header, not 403 — 403 is only for a recognized-but-
        # insufficient credential.
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_current_user_invalid_token(self, client):
        """Test /auth/me with invalid token fails."""
        response = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid_token_xyz"},
        )
        
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Invalid or expired token" in response.json()["detail"]
