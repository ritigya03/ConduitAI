import itertools

import pytest

import app.main as main_module


@pytest.fixture(autouse=True)
def _reset_request_counter():
    main_module._request_counter = itertools.count(1)
