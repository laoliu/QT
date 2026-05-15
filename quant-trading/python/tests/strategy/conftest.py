"""Shared fixtures for aipha strategy tests."""

import pytest


@pytest.fixture(autouse=True)
def _register_all_strategies():
    """Ensure all strategies are registered before each test."""
    import aipha.strategy.register_all  # noqa: F401
