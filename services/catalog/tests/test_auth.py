import pytest
from rest_framework_simplejwt.tokens import AccessToken

from .factories import PASSWORD, OrganizerFactory, UserFactory

pytestmark = pytest.mark.django_db


class TestRegister:
    def test_register_creates_user_with_hashed_password(self, api_client):
        response = api_client.post(
            "/api/auth/register/",
            {"username": "ana", "email": "ana@example.com", "password": PASSWORD},
        )
        assert response.status_code == 201
        assert "password" not in response.json()

    def test_weak_password_is_rejected(self, api_client):
        response = api_client.post(
            "/api/auth/register/", {"username": "ana", "password": "123"}
        )
        assert response.status_code == 400
        assert "password" in response.json()

    def test_registered_user_is_only_a_buyer(self, api_client):
        api_client.post(
            "/api/auth/register/", {"username": "ana", "password": PASSWORD}
        )
        token = api_client.post(
            "/api/auth/token/", {"username": "ana", "password": PASSWORD}
        ).json()["access"]
        assert AccessToken(token)["roles"] == ["buyer"]


class TestToken:
    def test_login_returns_access_and_refresh(self, api_client):
        user = UserFactory()
        response = api_client.post(
            "/api/auth/token/", {"username": user.username, "password": PASSWORD}
        )
        assert response.status_code == 200
        assert {"access", "refresh"} <= response.json().keys()

    def test_wrong_password_is_401(self, api_client):
        user = UserFactory()
        response = api_client.post(
            "/api/auth/token/", {"username": user.username, "password": "wrong"}
        )
        assert response.status_code == 401

    def test_organizer_token_carries_both_roles(self, api_client):
        organizer = OrganizerFactory()
        token = api_client.post(
            "/api/auth/token/", {"username": organizer.username, "password": PASSWORD}
        ).json()["access"]
        assert AccessToken(token)["roles"] == ["buyer", "organizer"]

    def test_refresh_returns_new_access_token(self, api_client):
        user = UserFactory()
        refresh = api_client.post(
            "/api/auth/token/", {"username": user.username, "password": PASSWORD}
        ).json()["refresh"]
        response = api_client.post("/api/auth/token/refresh/", {"refresh": refresh})
        assert response.status_code == 200
        assert "access" in response.json()


class TestMe:
    def test_me_requires_authentication(self, api_client):
        assert api_client.get("/api/auth/me/").status_code == 401

    def test_me_returns_user_and_roles(self, buyer_client, buyer):
        data = buyer_client.get("/api/auth/me/").json()
        assert data["username"] == buyer.username
        assert data["roles"] == ["buyer"]

    def test_tampered_token_is_rejected(self, api_client, buyer):
        token = str(AccessToken.for_user(buyer))
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token[:-4]}AAAA")
        assert api_client.get("/api/auth/me/").status_code == 401
