import pytest
from ingest_fakes import FakeProducer


@pytest.fixture
def producer() -> FakeProducer:
    return FakeProducer()
