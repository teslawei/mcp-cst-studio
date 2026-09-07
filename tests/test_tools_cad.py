"""Tests for the STP-side (OCC) cad tools.

Offline-first: when cadquery-ocp is not installed the tools must return a
clear install-hint error instead of crashing. When OCP IS available, a
tiny synthetic STP exercises the real recon path (no CST needed).
"""
import json
import pathlib

import pytest

from mcp_cst_studio.tools import cad


class _NullClient:
    """cad tools never touch CST; handler receives this placeholder."""


def _parse(result):
    return json.loads(result[0].text)


def test_tools_have_names_and_schemas():
    names = {t.name for t in cad.TOOLS}
    assert {"cst_stp_recon", "cst_stp_simplify"} <= names
    for t in cad.TOOLS:
        schema = getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
        assert schema is not None and schema.get("type") == "object"


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_json():
    res = await cad.handle("cst_no_such_tool", {}, _NullClient())
    data = _parse(res)
    assert data["status"] == "error"
    assert "cad" in data["message"]


@pytest.mark.asyncio
async def test_missing_ocp_returns_install_hint():
    if cad._HAS_OCP:
        pytest.skip("OCP installed; install-hint branch not active")
    res = await cad.handle(
        "cst_stp_recon", {"source_stp": "x.stp"}, _NullClient()
    )
    data = _parse(res)
    assert data["status"] == "error"
    assert "pip install cadquery-ocp" in data["message"]


@pytest.mark.asyncio
async def test_simplify_requires_whitelist():
    if not cad._HAS_OCP:
        pytest.skip("cadquery-ocp not installed")
    res = await cad.handle(
        "cst_stp_simplify",
        {"source_stp": "C:/nope/a.stp", "output_stp": "C:/nope/b.stp", "whitelist": {}},
        _NullClient(),
    )
    data = _parse(res)
    assert data["status"] == "error"
    assert "whitelist" in data["message"]


def test_recon_roundtrip_on_synthetic_stp():
    """With OCP present: write a 2-solid STP, recon must see both solids.

    Uses a self-managed artifact dir (not pytest tmp_path) so the test also
    runs inside restricted sandboxes where the tmpdir factory cannot list
    its temp root.
    """
    if not cad._HAS_OCP:
        pytest.skip("cadquery-ocp not installed")
    import asyncio
    import shutil
    import uuid
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
    from OCP.TopoDS import TopoDS_Compound
    from OCP.BRep import BRep_Builder

    art = pathlib.Path(__file__).parent / "_artifacts" / uuid.uuid4().hex[:8]
    art.mkdir(parents=True, exist_ok=True)
    try:
        b1 = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 10, 10).Shape()
        b2 = BRepPrimAPI_MakeBox(gp_Pnt(20, 0, 0), 5, 5, 5).Shape()
        comp = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(comp)
        builder.Add(comp, b1)
        builder.Add(comp, b2)
        stp = art / "two_boxes.stp"
        w = STEPControl_Writer()
        assert w.Transfer(comp, STEPControl_StepModelType.STEPControl_AsIs)
        assert w.Write(str(stp)) == IFSelect_RetDone

        res = asyncio.run(
            cad.handle("cst_stp_recon", {"source_stp": str(stp)}, _NullClient())
        )
        data = _parse(res)
        assert data["status"] == "ok"
        solids = [r for r in data["rows"] if r.get("solids")]
        assert len(solids) == 2
        vols = sorted(r["volume_mm3"] for r in solids)
        assert vols == [125, 1000]
    finally:
        shutil.rmtree(art.parent, ignore_errors=True)
