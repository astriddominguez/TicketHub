import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from .factories import OrganizerFactory, UserFactory


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    # In-memory cache, emptied per test: throttle counters must not leak between
    # tests (several tests log in), and the suite shouldn't need Redis running.
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


def _client_for(user) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")
    return client


@pytest.fixture
def buyer(db):
    return UserFactory()


@pytest.fixture
def organizer(db):
    return OrganizerFactory()


@pytest.fixture
def buyer_client(buyer) -> APIClient:
    return _client_for(buyer)


@pytest.fixture
def organizer_client(organizer) -> APIClient:
    return _client_for(organizer)
