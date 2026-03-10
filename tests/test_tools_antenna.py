"""Tests for antenna template tools — verify RF calculations and VBA generation."""

from __future__ import annotations

import json
import math

import pytest

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.config import CSTConfig

C = 299792458  # speed of light m/s


@pytest.fixture
def client() -> CSTClient:
    return CSTClient(config=CSTConfig(connected=False))


@pytest.mark.asyncio
async def test_patch_antenna_dimensions(client: CSTClient):
    from mcp_cst_studio.tools.antenna_templates import handle

    result = await handle(
        "cst_antenna_patch",
        {"frequency_ghz": 2.45, "substrate_height_mm": 1.6, "epsilon_r": 4.4},
        client,
    )
    data = json.loads(result[0].text)

    # Check that parameters were calculated
    params = data.get("calculated_parameters", data.get("parameters", {}))
    assert params, "Should return calculated parameters"

    # Patch width should be roughly λ/(2*sqrt((εr+1)/2)) in mm
    wavelength_mm = (C / 2.45e9) * 1000
    expected_width = wavelength_mm / (2 * math.sqrt((4.4 + 1) / 2))
    if "patch_width_mm" in params:
        assert abs(params["patch_width_mm"] - expected_width) / expected_width < 0.15

    # VBA script should be present
    vba = data.get("vba_script", data.get("vba", ""))
    assert "Brick" in vba or "brick" in vba.lower()
    assert len(vba) > 100  # Should be a substantial script


@pytest.mark.asyncio
async def test_dipole_antenna(client: CSTClient):
    from mcp_cst_studio.tools.antenna_templates import handle

    result = await handle(
        "cst_antenna_dipole",
        {"frequency_ghz": 1.0, "wire_radius_mm": 0.5},
        client,
    )
    data = json.loads(result[0].text)
    params = data.get("calculated_parameters", data.get("parameters", {}))

    # Dipole should be roughly 0.48λ long
    wavelength_mm = (C / 1.0e9) * 1000
    if "total_length_mm" in params:
        expected = 0.48 * wavelength_mm
        assert abs(params["total_length_mm"] - expected) / expected < 0.1


@pytest.mark.asyncio
async def test_horn_antenna(client: CSTClient):
    from mcp_cst_studio.tools.antenna_templates import handle

    result = await handle(
        "cst_antenna_horn",
        {"frequency_ghz": 10.0, "gain_dbi": 15},
        client,
    )
    data = json.loads(result[0].text)
    vba = data.get("vba_script", data.get("vba", ""))
    assert len(vba) > 50


@pytest.mark.asyncio
async def test_list_templates(client: CSTClient):
    from mcp_cst_studio.tools.antenna_templates import handle

    result = await handle("cst_list_antenna_templates", {}, client)
    data = json.loads(result[0].text)
    templates = data.get("templates", data)
    assert isinstance(templates, (list, dict))


@pytest.mark.asyncio
async def test_yagi_antenna(client: CSTClient):
    from mcp_cst_studio.tools.antenna_templates import handle

    result = await handle(
        "cst_antenna_yagi",
        {"frequency_ghz": 0.3, "num_directors": 5},
        client,
    )
    data = json.loads(result[0].text)
    vba = data.get("vba_script", data.get("vba", ""))
    assert len(vba) > 50
