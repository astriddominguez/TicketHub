from contextlib import contextmanager

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
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


@pytest.fixture
def assert_data_queries():
    """Like django_assert_num_queries, but ignores SAVEPOINT bookkeeping.

    With ATOMIC_REQUESTS each request runs in a transaction; inside a test (which
    is itself a transaction) that becomes SAVEPOINT + RELEASE. Those aren't data
    queries, and N+1 is about data queries.
    """

    @contextmanager
    def _assert(expected: int):
        with CaptureQueriesContext(connection) as context:
            yield
        data_queries = [
            query["sql"]
            for query in context.captured_queries
            if "SAVEPOINT" not in query["sql"]
        ]
        assert len(data_queries) == expected, "\n\n".join(data_queries)

    return _assert
