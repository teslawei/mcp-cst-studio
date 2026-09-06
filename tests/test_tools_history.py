"""Tests for history/recovery tools (offline mode + query validation)."""

from __future__ import annotations

import json

import pytest

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.tools.history import (
    _build_query_vba,
    _validate_query_expression,
    handle,
)


def _parse(result):
    return json.loads(result[0].text)


@pytest.mark.asyncio
async def test_full_history_rebuild_offline(offline_client: CSTClient):
    data = _parse(await handle("cst_full_history_rebuild", {}, offline_client))
    assert data["status"] == "offline"


@pytest.mark.asyncio
async def test_list_tree_items_offline(offline_client: CSTClient):
    data = _parse(await handle("cst_list_tree_items", {}, offline_client))
    assert data["status"] == "offline"


@pytest.mark.asyncio
async def test_solid_count_offline(offline_client: CSTClient):
    data = _parse(await handle("cst_solid_count", {}, offline_client))
    assert data["status"] == "offline"
    assert "vba" in data


@pytest.mark.asyncio
async def test_execute_vba_query_offline(offline_client: CSTClient):
    data = _parse(
        await handle(
            "cst_execute_vba_query",
            {"expression": "Solid.GetNumberOfShapes"},
            offline_client,
        )
    )
    assert data["status"] == "offline"
    assert "vba" in data


@pytest.mark.asyncio
async def test_execute_vba_query_rejects_file_io(mock_client: CSTClient):
    data = _parse(
        await handle(
            "cst_execute_vba_query",
            {"expression": 'Open "x.txt" For Output As #1'},
            mock_client,
        )
    )
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_execute_vba_query_rejects_shell(mock_client: CSTClient):
    data = _parse(
        await handle(
            "cst_execute_vba_query",
            {"expression": 'Shell("calc.exe")'},
            mock_client,
        )
    )
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_execute_vba_query_rejects_empty(mock_client: CSTClient):
    data = _parse(await handle("cst_execute_vba_query", {"expression": "  "}, mock_client))
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_execute_vba_query_connected(mock_client: CSTClient):
    """Connected-mode query writes the marker file that is then read back."""
    import os
    import re

    created: list[str] = []

    def fake_add_to_history(label, code, timeout=None):
        # Emulate CST executing the macro: parse the wrapper's output path
        # (the client derives it from a class-level counter) and create it.
        assert "Solid.GetNumberOfShapes" in code
        m = re.search(r'Open "([^"]+)" For Output', code)
        assert m, "wrapper must open a result file"
        out_path = m.group(1)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write("ok\n8\n")
        created.append(out_path)
        return None

    mock_client._project.model3d.add_to_history.side_effect = fake_add_to_history
    try:
        data = _parse(
            await handle(
                "cst_execute_vba_query",
                {"expression": "Solid.GetNumberOfShapes"},
                mock_client,
            )
        )
    finally:
        mock_client._project.model3d.add_to_history.side_effect = None
        for p in created:
            if os.path.exists(p):
                os.remove(p)

    assert data["status"] == "ok"
    assert data["value"] == "8"


@pytest.mark.asyncio
async def test_unknown_history_tool(offline_client: CSTClient):
    data = _parse(await handle("cst_nonexistent_tool", {}, offline_client))
    assert data["status"] == "error"


# ---------------------------------------------------------------------------
# Expression validator unit tests
# ---------------------------------------------------------------------------


def test_validator_accepts_readonly_expressions():
    assert _validate_query_expression("Solid.GetNumberOfShapes") is None
    assert (
        _validate_query_expression(
            'Solid.GetLooseBoundingBoxOfShape("comp:body", x1, x2, y1, y2, z1, z2)'
        )
        is None
    )


def test_validator_rejects_statements():
    assert _validate_query_expression('Open "f" For Output As #1') is not None
    assert _validate_query_expression('Shell("cmd")') is not None
    assert _validate_query_expression("Dim f As Integer") is not None
    assert _validate_query_expression("Close #1") is not None
    assert _validate_query_expression("") is not None


def test_validator_rejects_bad_chars():
    assert _validate_query_expression("a{b}") is not None
    assert _validate_query_expression("x" * 501) is not None


def test_query_vba_wrapper_contains_expression():
    vba = _build_query_vba("C:/out/q1.txt", "Solid.GetNumberOfShapes")
    assert "CStr(Solid.GetNumberOfShapes)" in vba
    assert "C:/out/q1.txt" in vba
