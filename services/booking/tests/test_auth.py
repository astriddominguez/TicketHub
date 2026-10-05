from datetime import timedelta

import jwt
from httpx import AsyncClient

from .conftest import make_token

URL = "/reservations"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_no_token_is_401(client: AsyncClient) -> None:
    response = await client.get(URL)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_valid_catalog_token_is_accepted(client: AsyncClient) -> None:
    response = await client.get(URL, headers=bearer(make_token(7)))
    assert response.status_code == 200


async def test_expired_token_is_401(client: AsyncClient) -> None:
    token = make_token(7, expires_in=timedelta(seconds=-1))
    response = await client.get(URL, headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["detail"] == "Token has expired."


async def test_token_signed_with_another_key_is_401(client: AsyncClient) -> None:
    token = make_token(7, key="not-the-shared-key-" + "x" * 32)
    assert (await client.get(URL, headers=bearer(token))).status_code == 401


async def test_refresh_token_cannot_call_the_api(client: AsyncClient) -> None:
    token = make_token(7, token_type="refresh")
    response = await client.get(URL, headers=bearer(token))
    assert response.status_code == 401
    assert response.json()["detail"] == "An access token is required."


async def test_unsigned_alg_none_token_is_rejected(client: AsyncClient) -> None:
    # Classic attack: claim "alg": "none" and send no signature at all.
    token = jwt.encode(
        {"user_id": "1", "token_type": "access", "exp": 9999999999},
        key=None,
        algorithm="none",
    )
    assert (await client.get(URL, headers=bearer(token))).status_code == 401
