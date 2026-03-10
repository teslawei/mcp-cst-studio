"""Shared test fixtures for CST Studio MCP server tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.config import CSTConfig


@pytest.fixture
def offline_config() -> CSTConfig:
    """Config for offline mode testing."""
    return CSTConfig(
        cst_path=None,
        work_dir="/tmp/cst_test",
        version="2026",
        connected=False,
    )


@pytest.fixture
def offline_client(offline_config: CSTConfig) -> CSTClient:
    """CSTClient in offline mode (no CST installation)."""
    return CSTClient(config=offline_config)


@pytest.fixture
def mock_client() -> CSTClient:
    """CSTClient with mocked CST connection for testing connected mode."""
    config = CSTConfig(
        cst_path=r"C:\Program Files\CST Studio Suite 2026",
        work_dir="/tmp/cst_test",
        version="2026",
        connected=True,
    )
    client = CSTClient(config=config)
    client._project = MagicMock()
    client._project_path = r"C:\test\project.cst"
    return client
