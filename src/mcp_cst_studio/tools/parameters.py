"""Parametric design tools for CST Studio Suite.

Provides 6 MCP tools for managing design parameters, setting up parameter
sweeps, and configuring optimizations in CST Studio.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.vba_builder import VBABuilder, VBAScript
from mcp_cst_studio.validators import validate_name, validate_positive

if TYPE_CHECKING:
    from mcp.server import Server

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # 1. Set parameter
    Tool(
        name="cst_set_parameter",
        description=(
            "Set or create a design parameter in CST Studio. Parameters can hold "
            "numeric values or string expressions referencing other parameters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Parameter name (e.g. 'patch_length', 'substrate_h').",
                },
                "value": {
                    "description": (
                        "Parameter value — a number (e.g. 10.5) or a string expression "
                        "referencing other parameters (e.g. 'patch_length / 2')."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "Optional human-readable description of the parameter.",
                },
            },
            "required": ["name", "value"],
        },
    ),

    # 2. Get parameter
    Tool(
        name="cst_get_parameter",
        description=(
            "Get the current value of a design parameter. Returns both the stored "
            "expression and the evaluated numeric value."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the parameter to retrieve.",
                },
            },
            "required": ["name"],
        },
    ),

    # 3. List parameters
    Tool(
        name="cst_list_parameters",
        description=(
            "List all design parameters in the current CST project with their "
            "names, expressions, and evaluated numeric values."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),

    # 4. Delete parameter
    Tool(
        name="cst_delete_parameter",
        description=(
            "Delete a design parameter from the CST project. The parameter must not "
            "be referenced by other parameters or geometry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the parameter to delete.",
                },
            },
            "required": ["name"],
        },
    ),

    # 5. Parameter sweep
    Tool(
        name="cst_parameter_sweep",
        description=(
            "Set up a parameter sweep in CST Studio. The sweep runs the simulation "
            "multiple times, varying the specified parameter across a range of values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "parameter": {
                    "type": "string",
                    "description": "Name of the parameter to sweep.",
                },
                "start": {
                    "type": "number",
                    "description": "Start value of the sweep range.",
                },
                "stop": {
                    "type": "number",
                    "description": "Stop value of the sweep range.",
                },
                "steps": {
                    "type": "integer",
                    "description": "Number of steps in the sweep (minimum 2).",
                },
            },
            "required": ["parameter", "start", "stop", "steps"],
        },
    ),

    # 6. Optimizer
    Tool(
        name="cst_optimizer",
        description=(
            "Set up an optimization in CST Studio. Define a goal (minimize, maximize, "
            "or target a specific value for a result), specify which parameters to vary "
            "with their bounds, and choose an optimization algorithm."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "goal_type": {
                    "type": "string",
                    "enum": ["minimize", "maximize", "target"],
                    "description": "Optimization goal type.",
                },
                "goal_value": {
                    "type": "number",
                    "description": (
                        "Target value for 'target' goal type. Ignored for minimize/maximize."
                    ),
                },
                "result_path": {
                    "type": "string",
                    "description": (
                        "Result tree path to optimize, e.g. "
                        "'1D Results\\S-Parameters\\S1,1' or '1D Results\\S-Parameters\\S2,1'."
                    ),
                },
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Parameter name.",
                            },
                            "min": {
                                "type": "number",
                                "description": "Minimum allowed value.",
                            },
                            "max": {
                                "type": "number",
                                "description": "Maximum allowed value.",
                            },
                        },
                        "required": ["name", "min", "max"],
                    },
                    "minItems": 1,
                    "description": "List of parameters to optimize with their min/max bounds.",
                },
                "method": {
                    "type": "string",
                    "enum": [
                        "Trust Region",
                        "Genetic Algorithm",
                        "Particle Swarm",
                        "Nelder Mead",
                    ],
                    "description": "Optimization algorithm.",
                    "default": "Trust Region",
                },
                "max_evaluations": {
                    "type": "integer",
                    "description": "Maximum number of solver evaluations.",
                    "default": 100,
                },
            },
            "required": ["goal_type", "result_path", "parameters"],
        },
    ),
]

_TOOL_NAMES = {t.name for t in TOOLS}

# ---------------------------------------------------------------------------
# VBA generation helpers
# ---------------------------------------------------------------------------


def _build_set_parameter(args: dict) -> str:
    """Build VBA script to set or create a design parameter."""
    name = validate_name(args["name"], "parameter name")
    value = args["value"]
    description = args.get("description")

    script = VBAScript()
    script.add_comment(f"Set parameter: {name} = {value}")

    # Use MakeSureParameterExists to create if missing, then StoreParameter to set
    lines = [
        f'MakeSureParameterExists "{name}", "{value}"',
        f'StoreParameter "{name}", "{value}"',
    ]
    if description:
        lines.append(f'SetParameterDescription "{name}", "{description}"')

    script.add_raw("\n".join(lines))

    # Rebuild to apply the parameter change
    script.add_raw("RebuildOnParametricChange False, True")

    return script.build()


def _build_get_parameter(args: dict) -> str:
    """Build VBA script to retrieve a parameter value."""
    name = validate_name(args["name"], "parameter name")

    script = VBAScript()
    script.add_comment(f"Get parameter: {name}")

    lines = [
        "Dim dValue As Double",
        f'dValue = RestoreParameter("{name}")',
        f'MsgBox "Parameter {name} = " & CStr(dValue)',
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_list_parameters(args: dict) -> str:
    """Build VBA script to list all design parameters."""
    script = VBAScript()
    script.add_comment("List all design parameters")

    lines = [
        "Dim nParams As Long",
        "nParams = GetNumberOfParameters()",
        "Dim i As Long",
        "For i = 0 To nParams - 1",
        "  Dim sName As String",
        "  sName = GetParameterName(i)",
        "  Dim dValue As Double",
        "  dValue = GetParameterNValue(i)",
        '  Debug.Print sName & " = " & CStr(dValue)',
        "Next i",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_delete_parameter(args: dict) -> str:
    """Build VBA script to delete a parameter."""
    name = validate_name(args["name"], "parameter name")

    script = VBAScript()
    script.add_comment(f"Delete parameter: {name}")
    script.add_raw(f'DeleteParameter "{name}"')
    script.add_raw("RebuildOnParametricChange False, True")
    return script.build()


def _build_parameter_sweep(args: dict) -> str:
    """Build VBA script to configure a parameter sweep."""
    parameter = validate_name(args["parameter"], "parameter name")
    start = float(args["start"])
    stop = float(args["stop"])
    steps = int(args["steps"])

    if steps < 2:
        raise ValueError("Parameter sweep requires at least 2 steps")

    script = VBAScript()
    script.add_comment(f"Parameter sweep: {parameter} from {start} to {stop} in {steps} steps")

    vba = (
        VBABuilder("ParameterSweep")
        .call("Reset")
        .set("SimulationType", "Transient")
        .call_with_args("AddParameter_Linear", parameter, str(start), str(stop), str(steps))
        .call("Create")
    )
    script.add_block(vba)
    return script.build()


def _build_optimizer(args: dict) -> str:
    """Build VBA script to configure an optimization."""
    goal_type = args["goal_type"]
    goal_value = args.get("goal_value", 0)
    result_path = args["result_path"]
    parameters = args["parameters"]
    method = args.get("method", "Trust Region")
    max_evaluations = int(args.get("max_evaluations", 100))

    if goal_type not in ("minimize", "maximize", "target"):
        raise ValueError(f"Invalid goal_type '{goal_type}'. Must be minimize, maximize, or target")
    if not parameters:
        raise ValueError("At least one parameter must be specified for optimization")
    validate_positive(max_evaluations, "max_evaluations")

    script = VBAScript()
    script.add_comment(f"Optimization: {goal_type} {result_path}")

    # Configure the optimizer object
    vba = (
        VBABuilder("Optimizer")
        .call("Reset")
        .set("SetOptimizerType", method)
        .set_number("SetMaxEvaluations", max_evaluations)
    )

    # Set the optimization goal
    goal_map = {
        "minimize": "Min",
        "maximize": "Max",
        "target": "Target",
    }
    vba.set("SetGoalType", goal_map[goal_type])
    vba.set("SetGoalResult", result_path)

    if goal_type == "target":
        vba.set_number("SetGoalTarget", goal_value)

    vba.call("InitGoal")

    # Add parameters with ranges
    for param in parameters:
        param_name = validate_name(param["name"], "optimizer parameter name")
        param_min = float(param["min"])
        param_max = float(param["max"])
        if param_min >= param_max:
            raise ValueError(
                f"Parameter '{param_name}' min ({param_min}) must be less than max ({param_max})"
            )
        vba.call_with_args("AddParameter", param_name, str(param_min), str(param_max))

    vba.call("Start")
    script.add_block(vba)
    return script.build()


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, callable] = {
    "cst_set_parameter": _build_set_parameter,
    "cst_get_parameter": _build_get_parameter,
    "cst_list_parameters": _build_list_parameters,
    "cst_delete_parameter": _build_delete_parameter,
    "cst_parameter_sweep": _build_parameter_sweep,
    "cst_optimizer": _build_optimizer,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def _text(data: dict) -> list[TextContent]:
    """Wrap a dict as a single JSON TextContent response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a parameter tool call.

    Generates VBA via VBABuilder, executes through the CSTClient, and
    returns the result wrapped in TextContent.
    """
    builder_fn = _HANDLERS.get(name)
    if builder_fn is None:
        return _text({"status": "error", "message": f"Unknown parameter tool: {name}"})

    try:
        vba_code = builder_fn(arguments)
        result = client.execute_vba(vba_code)

        # Annotate result with tool-specific metadata
        if name == "cst_set_parameter":
            result["parameter"] = arguments["name"]
            result["value"] = arguments["value"]
        elif name == "cst_get_parameter":
            result["parameter"] = arguments["name"]
        elif name == "cst_delete_parameter":
            result["parameter"] = arguments["name"]
        elif name == "cst_parameter_sweep":
            result["parameter"] = arguments["parameter"]
            result["start"] = arguments["start"]
            result["stop"] = arguments["stop"]
            result["steps"] = arguments["steps"]
        elif name == "cst_optimizer":
            result["goal_type"] = arguments["goal_type"]
            result["result_path"] = arguments["result_path"]
            result["method"] = arguments.get("method", "Trust Region")
            result["max_evaluations"] = arguments.get("max_evaluations", 100)
            result["parameters"] = [p["name"] for p in arguments["parameters"]]

        return _text(result)
    except Exception as e:
        return _text({"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# Registration helper (called from tools/__init__.py)
# ---------------------------------------------------------------------------


def register_parameter_tools(server: Server, client: CSTClient) -> None:
    """Register parameter tools with the MCP server."""
    from mcp_cst_studio.tools import _registry
    _registry.add_module(TOOLS, handle, client)
