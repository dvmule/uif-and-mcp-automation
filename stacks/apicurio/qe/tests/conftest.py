"""Shared pytest fixtures."""

import copy

import pytest

import app.clients.cmdb_client as _cmdb_module


@pytest.fixture(autouse=True)
def reset_cmdb_fixtures():
    """Restore the in-memory CMDB fixture dict after each test.

    Route tests that call simulate_deploy mutate _FIXTURES via update_manifest.
    Without this fixture those mutations bleed into subsequent tests.
    """
    snapshot = copy.deepcopy(_cmdb_module._FIXTURES)
    yield
    _cmdb_module._FIXTURES.clear()
    _cmdb_module._FIXTURES.update(snapshot)
