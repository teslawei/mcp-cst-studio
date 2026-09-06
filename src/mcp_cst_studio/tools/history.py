"""History and recovery tools for CST Studio MCP server.

Provides 4 MCP tools covering the modeler-history recovery workflow that is
otherwise impossible to perform safely from automation:

- ``cst_full_history_rebuild`` — replay the complete modeler history in place
  (the programmatic equivalent of the GUI History List "Rebuild").  This is
  the *only* reliable way to re-create geometry that a later history entry
  deleted, and the primary recovery path after a bad boolean/delete mistake.
- ``cst_list_tree_items`` — enumerate the real navigation tree via the
  documented ``model3d.get_tree_items()`` API.  Unlike ``SelectTreeItem``
  probing (which returns 0 even for non-existent names), this never yields
  false positives, making it the correct primitive for component audits.
- ``cst_solid_count`` — number of solids in the 3D model, via the
  marker-file VBA bridge.  The single most useful sanity probe after any
  history/boolean operation.
- ``cst_execute_vba_query`` — evaluate a read-only VBA expression and return
  its value.  CST's ``add_to_history`` cannot return values to the caller,
  so the expression is wrapped in a tool-generated macro that writes the
  result to a marker file which this module then reads back.

See ``docs/HISTORY_AND_RECOVERY.md`` for the full methodology and the
field-tested pitfalls behind each tool.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.validators import validate_vba_input

if TYPE_CHECKING:
    from mcp.server import Server

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="cst_full_history_rebuild",
        description=(
            "Replay the complete modeler history of the open project in place "
            "(equivalent to the GUI History List rebuild, no restart needed). "
            "Use to recover geometry deleted by a later history entry, or to "
            "re-apply a clean history after it was corrected. Note: "
            "IsBuildingModel() may keep reporting True after completion (stale "
            "flag) - verify the result with cst_solid_count instead. Large "
            "models can take many minutes to replay."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Seconds to wait for the rebuild before giving up. "
                        "Default 1800 (30 min) for large assemblies."
                    ),
                    "default": 1800,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_list_tree_items",
        description=(
            "Enumerate the real navigation tree of the open project via the "
            "documented get_tree_items() API (flat list of all tree paths). "
            "This is the authoritative component/material listing: unlike "
            "SelectTreeItem probing it never reports false positives. Use "
            "prefix 'Components' to audit components, e.g. for empty leftover "
            "components after simplification."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prefix": {
                    "type": "string",
                    "description": (
                        "Optional path prefix filter, e.g. 'Components' "
                        "returns only component entries. Omit for all items."
                    ),
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_solid_count",
        description=(
            "Return the number of solids currently in the 3D model. Fast "
            "sanity probe to run after any boolean operation, deletion, or "
            "history rebuild (e.g. expect 8 after a recovery rebuild that "
            "restored a deleted solid)."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="cst_execute_vba_query",
        description=(
            "Evaluate a read-only VBA expression in the open model and return "
            "its string value. CST history macros cannot return values, so "
            "the expression is wrapped by the tool into a macro that writes "
            "the result to a marker file which is read back. The expression "
            "must be side-effect free, e.g. 'Solid.GetNumberOfShapes' or a "
            "bbox probe via GetLooseBoundingBoxOfShape. File I/O and shell "
            "calls in the expression are rejected. WARNING: bounding boxes "
            "of B-spline solids are control-point hulls - they overestimate "
            "the real extent and must never be used to infer that one solid "
            "contains another."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": (
                        "Read-only VBA expression to evaluate, e.g. "
                        "'Solid.GetNumberOfShapes'. Multi-statement probes "
                        "are not allowed here - use cst_execute_vba for "
                        "side effects and this tool for values."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Seconds to wait for the query macro. Default 60."
                    ),
                    "default": 60,
                },
            },
            "required": ["expression"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Expression validation for the query bridge
# ---------------------------------------------------------------------------

# Characters allowed in a query expression: identifiers, dots, parens,
# commas, string literals, arithmetic/comparison operators, spaces.
_QUERY_EXPR_ALLOWED = re.compile(r"^[A-Za-z0-9_ .,()\"'+\-*/\\:&]+$")

# Statements that must never appear in an expression (file I/O, shell,
# declaration, process control).  Checked case-insensitively on word
# boundaries so e.g. 'Open' inside 'GetOpenProjects' does not trigger.
_QUERY_EXPR_DENIED = re.compile(
    r"\b(open|close|shell|createobject|declare|sendkeys|kill|mkdir|rmdir|"
    r"setattr|appactivate|put|get #|input|write|print|line|name|"
    r"dim|const|redim|erase|set|let|call|execute|eval|run|do|loop|while|"
    r"for|each|next|if|then|else|end|on|error|goto|exit|stop)\b",
    re.IGNORECASE,
)

_MAX_EXPR_LEN = 500


def _validate_query_expression(expression: str) -> str | None:
    """Return an error message if *expression* is not a safe query, else None."""
    if not expression or not expression.strip():
        return "expression cannot be empty"
    if len(expression) > _MAX_EXPR_LEN:
        return f"expression too long (max {_MAX_EXPR_LEN} chars)"
    if not _QUERY_EXPR_ALLOWED.match(expression):
        return "expression contains characters outside the allowed set"
    # Deny statement keywords only when they appear as standalone function
    # call/open statements; allow method names that merely contain them.
    if _QUERY_EXPR_DENIED.search(expression):
        # Allow dotted member access like Solid.GetLooseBoundingBoxOfShape
        # (the denied words must not directly follow a dot or quote-start).
        stripped = re.sub(r"\.\s*[A-Za-z_][A-Za-z0-9_]*", "", expression)
        if _QUERY_EXPR_DENIED.search(stripped):
            return (
                "expression must be a read-only value expression; "
                "file I/O, shell, and statement keywords are not allowed"
            )
    return None


def _build_query_vba(out_path: str, expression: str) -> str:
    """Build the marker-file VBA wrapper for *expression*.

    The wrapper is generated here (trusted code); the user expression has
    already passed ``_validate_query_expression``.  Result file layout:
    first line ``ok`` or ``error:<msg>``, second line the value.
    """
    escaped_path = out_path.replace('"', '""')
    return (
        "On Error Resume Next\n"
        "Dim f As Integer\n"
        "f = FreeFile\n"
        f'Open "{escaped_path}" For Output As #f\n'
        "If Err.Number <> 0 Then\n"
        '  Print #f, "error"\n'
        f'  Print #f, "cannot open result file"\n'
        "  Close #f\n"
        "  On Error GoTo 0\n"
        "Else\n"
        f'  Print #f, "ok"\n'
        f"  Print #f, CStr({expression})\n"
        "  Close #f\n"
        "End If\n"
        "On Error GoTo 0\n"
    )


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


def _text(data: dict) -> list[TextContent]:
    """Wrap a dict as a single JSON TextContent response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2, default=str))]


def _handle_full_history_rebuild(args: dict, client: CSTClient) -> dict:
    timeout = int(args.get("timeout", 1800))
    result = client.full_history_rebuild(timeout=timeout)
    if result.get("status") == "executed":
        result["note"] = (
            "IsBuildingModel() can stay True after completion (stale flag). "
            "Verify with cst_solid_count; save with cst_save_project "
            "(model3d.Save) once the solid count is as expected."
        )
    return result


def _handle_list_tree_items(args: dict, client: CSTClient) -> dict:
    prefix = args.get("prefix", "")
    return client.get_tree_items(prefix=prefix or None)


def _handle_solid_count(args: dict, client: CSTClient) -> dict:
    result = client.execute_vba_query("Solid.GetNumberOfShapes", timeout=60)
    if result.get("status") == "ok":
        try:
            result["solid_count"] = int(result.pop("value"))
        except (KeyError, ValueError):
            result["status"] = "error"
            result["message"] = (
                f"Unexpected query output: {result.get('value')!r}"
            )
    return result


def _handle_execute_vba_query(args: dict, client: CSTClient) -> dict:
    expression = args.get("expression", "")
    timeout = int(args.get("timeout", 60))

    error = _validate_query_expression(expression)
    if error:
        return {"status": "error", "message": error}

    # Belt and braces: the same scanner used for raw VBA also runs on the
    # expression (it blocks shell/createobject/declare/sendkeys outright).
    try:
        validate_vba_input(expression)
    except Exception as exc:
        return {"status": "error", "message": f"expression rejected: {exc}"}

    return client.execute_vba_query(expression, timeout=timeout)


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a history/recovery tool call."""
    try:
        if name == "cst_full_history_rebuild":
            result = _handle_full_history_rebuild(arguments, client)
        elif name == "cst_list_tree_items":
            result = _handle_list_tree_items(arguments, client)
        elif name == "cst_solid_count":
            result = _handle_solid_count(arguments, client)
        elif name == "cst_execute_vba_query":
            result = _handle_execute_vba_query(arguments, client)
        else:
            result = {"status": "error", "message": f"Unknown history tool: {name}"}
        return _text(result)
    except Exception as e:  # noqa: BLE001 - contract: never raise from handle()
        return _text({"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# Registration helper (called from tools/__init__.py)
# ---------------------------------------------------------------------------


def register_history_tools(server: Server, client: CSTClient) -> None:
    """Register history/recovery tools with the MCP server."""
    from mcp_cst_studio.tools import _registry
    _registry.add_module(TOOLS, handle, client)
