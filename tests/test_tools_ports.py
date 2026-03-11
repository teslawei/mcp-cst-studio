"""Tests for port and excitation tools."""
from __future__ import annotations

import json

import pytest

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.config import CSTConfig


@pytest.fixture
def client() -> CSTClient:
    return CSTClient(config=CSTConfig(connected=False))


@pytest.mark.asyncio
async def test_add_waveguide_port(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle(
        "cst_add_waveguide_port",
        {
            "port_number": 1,
            "orientation": "zmin",
            "x_min": -5,
            "x_max": 5,
            "y_min": -5,
            "y_max": 5,
            "z_min": 0,
            "z_max": 0,
        },
        client,
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    vba = data.get("vba", "")
    assert "Port" in vba


@pytest.mark.asyncio
async def test_add_discrete_port(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle(
        "cst_add_discrete_port",
        {
            "port_number": 1,
            "type": "SParameter",
            "x1": 0,
            "y1": 0,
            "z1": 0,
            "x2": 0,
            "y2": 0,
            "z2": 1,
            "impedance": 50,
        },
        client,
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    vba = data.get("vba", "")
    assert "DiscretePort" in vba


@pytest.mark.asyncio
async def test_add_plane_wave(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle(
        "cst_add_plane_wave",
        {"theta": 0, "phi": 0, "polarization": "linear"},
        client,
    )
    assert len(result) == 1
    data = json.loads(result[0].text)
    vba = data.get("vba", "")
    assert "PlaneWave" in vba


@pytest.mark.asyncio
async def test_list_ports(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle("cst_list_ports", {}, client)
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert "status" in data


@pytest.mark.asyncio
async def test_delete_port(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle("cst_delete_port", {"port_number": 1}, client)
    assert len(result) == 1
    data = json.loads(result[0].text)
    vba = data.get("vba", "")
    assert "Port" in vba
    assert "Delete" in vba


@pytest.mark.asyncio
async def test_unknown_tool_returns_error(client: CSTClient):
    from mcp_cst_studio.tools.ports import handle

    result = await handle("cst_nonexistent_port_tool", {}, client)
    assert len(result) == 1
    data = json.loads(result[0].text)
    assert data.get("status") == "error"
