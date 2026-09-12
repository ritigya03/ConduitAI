from uuid import uuid4

import pytest


@pytest.fixture
def unique_tenant_id() -> str:
    return f"test-tenant-{uuid4()}"
