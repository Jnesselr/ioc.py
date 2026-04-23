import pytest

from ioc import Resolver


@pytest.fixture
def resolver() -> Resolver:
    return Resolver()


@pytest.fixture(autouse=True)
def _reset_global_resolver():
    Resolver.reset()
    yield
    Resolver.reset()
