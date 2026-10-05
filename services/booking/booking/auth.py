from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from booking.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    roles: tuple[str, ...]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> CurrentUser:
    """Trust the catalog's token: it is signed with the shared key.

    No database lookup and no call to the catalog: the user id and roles travel
    inside the token, so this service keeps working even if the catalog is down.
    """
    if credentials is None:
        raise _unauthorized("Authentication credentials were not provided.")
    settings = get_settings()
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_signing_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # never trust the token's own "alg"
            options={"require": ["exp", "user_id", "token_type"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired.") from None
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token.") from None

    # A refresh token is also signed by the catalog, but it is not meant for API calls.
    if claims["token_type"] != "access":
        raise _unauthorized("An access token is required.")
    return CurrentUser(id=int(claims["user_id"]), roles=tuple(claims.get("roles", ())))


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
