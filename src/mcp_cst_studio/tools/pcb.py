"""PCB layout and signal integrity tools for CST Studio Suite.

Provides 6 MCP tools for creating PCB stackups, traces, vias, ground planes,
importing Gerber files, and listing predefined stackup templates.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import Tool, TextContent

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.vba_builder import VBABuilder, VBAScript
from mcp_cst_studio.validators import validate_name, validate_positive

if TYPE_CHECKING:
    from mcp.server import Server

DATA_DIR = Path(__file__).parent.parent / "data"

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # 1. Create PCB stackup
    Tool(
        name="cst_pcb_create_stackup",
        description=(
            "Create a PCB layer stackup in CST Studio. Generates brick geometry for "
            "each layer (signal, ground, power, dielectric) positioned vertically with "
            "correct materials. Returns total thickness and layer positions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "layers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Layer name (e.g. 'Top', 'Core', 'GND')",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["signal", "ground", "power", "dielectric"],
                                "description": "Layer type",
                            },
                            "thickness_mm": {
                                "type": "number",
                                "description": "Layer thickness in mm (e.g. 0.035 for 1oz copper)",
                            },
                            "material": {
                                "type": "string",
                                "description": "Material name (e.g. 'Copper', 'FR-4')",
                            },
                            "epsilon_r": {
                                "type": "number",
                                "description": "Relative permittivity (1.0 for copper, 4.4 for FR-4)",
                            },
                        },
                        "required": ["name", "type", "thickness_mm", "material", "epsilon_r"],
                    },
                    "minItems": 2,
                    "description": (
                        "Ordered list of layers from top to bottom. Example 4-layer: "
                        "signal-dielectric-ground-dielectric(core)-power-dielectric-signal"
                    ),
                },
                "board_width_mm": {
                    "type": "number",
                    "description": "Board width in mm (X dimension)",
                },
                "board_length_mm": {
                    "type": "number",
                    "description": "Board length in mm (Y dimension)",
                },
            },
            "required": ["layers", "board_width_mm", "board_length_mm"],
        },
    ),

    # 2. Create PCB trace
    Tool(
        name="cst_pcb_create_trace",
        description=(
            "Create a PCB trace (microstrip, stripline, coplanar waveguide, or grounded "
            "CPW) in CST Studio. Optionally calculates trace width from a target impedance "
            "using Hammerstad-Jensen (microstrip) or Cohn (stripline) formulas."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "trace_type": {
                    "type": "string",
                    "enum": ["microstrip", "stripline", "coplanar_waveguide", "grounded_cpw"],
                    "description": "Type of transmission line",
                },
                "width_mm": {
                    "type": "number",
                    "description": (
                        "Trace width in mm. If impedance_target is specified, this is "
                        "ignored and the width is calculated automatically."
                    ),
                },
                "length_mm": {
                    "type": "number",
                    "description": "Trace length in mm",
                },
                "layer": {
                    "type": "string",
                    "description": "Layer name where the trace is placed (e.g. 'Top')",
                },
                "x_start": {
                    "type": "number",
                    "description": "X coordinate of trace start (mm)",
                    "default": 0,
                },
                "y_start": {
                    "type": "number",
                    "description": "Y coordinate of trace start (mm)",
                    "default": 0,
                },
                "direction": {
                    "type": "string",
                    "description": (
                        "Trace direction: 'x' for +X, 'y' for +Y, or angle in degrees "
                        "from +X axis (e.g. '45')"
                    ),
                    "default": "x",
                },
                "impedance_target": {
                    "type": "number",
                    "description": (
                        "Target characteristic impedance in ohms. When specified, trace "
                        "width is auto-calculated. Requires substrate_height_mm and epsilon_r."
                    ),
                },
                "substrate_height_mm": {
                    "type": "number",
                    "description": (
                        "Substrate height between trace and reference plane (mm). "
                        "Required when impedance_target is given."
                    ),
                },
                "epsilon_r": {
                    "type": "number",
                    "description": (
                        "Substrate relative permittivity. Required when impedance_target is given."
                    ),
                },
                "copper_thickness_mm": {
                    "type": "number",
                    "description": "Copper thickness in mm (default 0.035 for 1oz)",
                    "default": 0.035,
                },
                "z_position": {
                    "type": "number",
                    "description": (
                        "Z position of the trace bottom surface in mm. If omitted, "
                        "defaults to 0 (top surface of the board)."
                    ),
                    "default": 0,
                },
            },
            "required": ["trace_type", "length_mm", "layer"],
        },
    ),

    # 3. Create PCB via
    Tool(
        name="cst_pcb_create_via",
        description=(
            "Create a PCB via (through, blind, or buried) in CST Studio. Generates a "
            "cylindrical barrel, annular pads on signal layers, and antipads in "
            "ground/power planes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "x": {
                    "type": "number",
                    "description": "Via X position in mm",
                },
                "y": {
                    "type": "number",
                    "description": "Via Y position in mm",
                },
                "drill_diameter_mm": {
                    "type": "number",
                    "description": "Drill hole diameter in mm",
                    "default": 0.3,
                },
                "pad_diameter_mm": {
                    "type": "number",
                    "description": "Annular pad diameter in mm",
                    "default": 0.6,
                },
                "antipad_diameter_mm": {
                    "type": "number",
                    "description": "Antipad (clearance) diameter in plane layers in mm",
                    "default": 0.8,
                },
                "start_layer": {
                    "type": "string",
                    "description": "Name of the starting layer (e.g. 'Top')",
                },
                "end_layer": {
                    "type": "string",
                    "description": "Name of the ending layer (e.g. 'Bottom')",
                },
                "start_z": {
                    "type": "number",
                    "description": "Z coordinate of the via start (top) in mm",
                },
                "end_z": {
                    "type": "number",
                    "description": "Z coordinate of the via end (bottom) in mm",
                },
                "via_type": {
                    "type": "string",
                    "enum": ["through", "blind", "buried"],
                    "description": "Via type",
                    "default": "through",
                },
                "name": {
                    "type": "string",
                    "description": "Via name for the CST model (auto-generated if omitted)",
                },
            },
            "required": ["x", "y", "start_layer", "end_layer", "start_z", "end_z"],
        },
    ),

    # 4. Create ground/power plane
    Tool(
        name="cst_pcb_create_ground_plane",
        description=(
            "Create a ground or power plane with optional cutouts (split planes, "
            "isolation slots) in CST Studio. Generates a solid copper brick and "
            "subtracts cutout regions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "layer": {
                    "type": "string",
                    "description": "Layer name (e.g. 'GND', 'PWR')",
                },
                "width_mm": {
                    "type": "number",
                    "description": "Plane width in mm (X dimension)",
                },
                "length_mm": {
                    "type": "number",
                    "description": "Plane length in mm (Y dimension)",
                },
                "z_position": {
                    "type": "number",
                    "description": "Z position of the plane bottom surface in mm",
                    "default": 0,
                },
                "thickness_mm": {
                    "type": "number",
                    "description": "Copper thickness in mm (default 0.035 for 1oz)",
                    "default": 0.035,
                },
                "cutouts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "x_min": {"type": "number", "description": "Cutout X minimum in mm"},
                            "x_max": {"type": "number", "description": "Cutout X maximum in mm"},
                            "y_min": {"type": "number", "description": "Cutout Y minimum in mm"},
                            "y_max": {"type": "number", "description": "Cutout Y maximum in mm"},
                        },
                        "required": ["x_min", "x_max", "y_min", "y_max"],
                    },
                    "description": "Optional list of rectangular cutout regions",
                },
            },
            "required": ["layer", "width_mm", "length_mm"],
        },
    ),

    # 5. Import Gerber files
    Tool(
        name="cst_pcb_import_gerber",
        description=(
            "Import a Gerber/ODB++/DXF file for PCB analysis in CST Studio. Generates "
            "VBA for the CST Gerber import wizard. In offline mode, explains the import "
            "process and required settings."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the Gerber file (.gbr, .ger, .gtl, .gbl, etc.)",
                },
                "layer_name": {
                    "type": "string",
                    "description": "Target layer name in the CST model",
                },
                "file_type": {
                    "type": "string",
                    "enum": ["gerber", "odb++", "dxf"],
                    "description": "Import file format",
                    "default": "gerber",
                },
            },
            "required": ["file_path", "layer_name"],
        },
    ),

    # 6. List stackup templates
    Tool(
        name="cst_pcb_list_stackup_templates",
        description=(
            "List predefined PCB stackup templates with complete layer definitions. "
            "Includes standard 2/4/6-layer FR-4 and RF-grade Rogers stackups. "
            "Use the returned layer data directly with cst_pcb_create_stackup."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": (
                        "Optional filter string to match template names "
                        "(e.g. '4-layer', 'Rogers', 'RF'). Case-insensitive."
                    ),
                },
            },
            "required": [],
        },
    ),
]

_TOOL_NAMES = {t.name for t in TOOLS}

# ---------------------------------------------------------------------------
# Impedance calculation helpers
# ---------------------------------------------------------------------------


def _microstrip_impedance(w: float, h: float, er: float, t: float = 0.035) -> float:
    """Calculate microstrip characteristic impedance using Hammerstad-Jensen.

    Parameters
    ----------
    w : float  - trace width (mm)
    h : float  - substrate height (mm)
    er : float - relative permittivity
    t : float  - conductor thickness (mm)

    Returns
    -------
    float - characteristic impedance in ohms
    """
    # Effective width correction for finite conductor thickness
    if t > 0 and w > 0:
        dw = (t / math.pi) * (1.0 + math.log(2.0 * h / t))
        we = w + dw
    else:
        we = w

    u = we / h

    # Hammerstad-Jensen effective dielectric constant
    f_u = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-(30.666 / u) ** 0.7528)
    eps_eff = 0.5 * (er + 1.0) + 0.5 * (er - 1.0) * (1.0 + 10.0 / u) ** (-0.5 * f_u)  # noqa: E501

    # Hammerstad-Jensen impedance
    f = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-(30.666 / u) ** 0.7528)
    z0 = (60.0 / math.sqrt(eps_eff)) * math.log(f / u + math.sqrt(1.0 + (2.0 / u) ** 2))  # noqa: E501

    return z0


def _microstrip_width_for_impedance(
    z_target: float, h: float, er: float, t: float = 0.035
) -> float:
    """Calculate microstrip width for a target impedance (iterative).

    Uses Newton-Raphson iteration starting from the Wheeler approximation.

    Parameters
    ----------
    z_target : float - target impedance (ohms)
    h : float        - substrate height (mm)
    er : float       - relative permittivity
    t : float        - conductor thickness (mm)

    Returns
    -------
    float - trace width in mm
    """
    # Wheeler initial estimate
    a = (z_target / 60.0) * math.sqrt((er + 1.0) / 2.0) + \
        ((er - 1.0) / (er + 1.0)) * (0.23 + 0.11 / er)
    b = 377.0 * math.pi / (2.0 * z_target * math.sqrt(er))

    if a > 1.52:
        w_h = 8.0 * math.exp(a) / (math.exp(2.0 * a) - 2.0)
    else:
        w_h = (2.0 / math.pi) * (
            b - 1.0 - math.log(2.0 * b - 1.0)
            + ((er - 1.0) / (2.0 * er)) * (math.log(b - 1.0) + 0.39 - 0.61 / er)
        )

    w = w_h * h

    # Refine with Newton-Raphson (up to 20 iterations)
    for _ in range(20):
        z_calc = _microstrip_impedance(w, h, er, t)
        error = z_calc - z_target
        if abs(error) < 0.01:
            break
        # Numerical derivative
        dw = w * 0.001
        z_plus = _microstrip_impedance(w + dw, h, er, t)
        dz_dw = (z_plus - z_calc) / dw
        if abs(dz_dw) < 1e-12:
            break
        w = w - error / dz_dw
        w = max(w, 0.001)  # Prevent negative width

    return w


def _stripline_impedance(w: float, h: float, er: float, t: float = 0.035) -> float:
    """Calculate stripline characteristic impedance using Cohn's formula.

    Parameters
    ----------
    w : float  - trace width (mm)
    h : float  - total distance between ground planes (mm)
    er : float - relative permittivity
    t : float  - conductor thickness (mm)

    Returns
    -------
    float - characteristic impedance in ohms
    """
    b = h  # distance between ground planes
    # Effective width correction (Cohn)
    if t > 0 and t < b:
        m = 2.0 / (1.0 + t / b)
        we = w + (t / math.pi) * (1.0 + math.log(4.0 * math.pi * w / t)) * (m / 2.0)
    else:
        we = w

    # Cohn formula for centered stripline
    if we / b < 0.35:
        z0 = (60.0 / math.sqrt(er)) * math.log(
            4.0 * b / (math.pi * we)
        )
    else:
        cf = 2.0 * math.pi
        z0 = (94.15 / math.sqrt(er)) / (
            we / b + cf * math.log(1.0 + 1.0 / math.tanh(cf * we / (2.0 * b)))  # noqa: E501
            / math.pi
        )

    return z0


def _stripline_width_for_impedance(
    z_target: float, h: float, er: float, t: float = 0.035
) -> float:
    """Calculate stripline width for a target impedance (iterative).

    Parameters
    ----------
    z_target : float - target impedance (ohms)
    h : float        - total distance between ground planes (mm)
    er : float       - relative permittivity
    t : float        - conductor thickness (mm)

    Returns
    -------
    float - trace width in mm
    """
    # Initial estimate from inverted Cohn narrow-strip formula
    x = math.exp(z_target * math.sqrt(er) / 60.0)
    w = 4.0 * h / (math.pi * x) if x > 0 else h * 0.5

    # Newton-Raphson refinement
    for _ in range(20):
        z_calc = _stripline_impedance(w, h, er, t)
        error = z_calc - z_target
        if abs(error) < 0.01:
            break
        dw = w * 0.001
        z_plus = _stripline_impedance(w + dw, h, er, t)
        dz_dw = (z_plus - z_calc) / dw
        if abs(dz_dw) < 1e-12:
            break
        w = w - error / dz_dw
        w = max(w, 0.001)

    return w


# ---------------------------------------------------------------------------
# VBA generation handlers
# ---------------------------------------------------------------------------


async def _handle_create_stackup(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Create a PCB layer stackup as stacked bricks in CST."""
    layers: list[dict] = arguments["layers"]
    board_w = validate_positive(float(arguments["board_width_mm"]), "board_width_mm")
    board_l = validate_positive(float(arguments["board_length_mm"]), "board_length_mm")

    script = VBAScript()
    script.add_comment("PCB Layer Stackup")

    half_w = board_w / 2.0
    half_l = board_l / 2.0
    z_current = 0.0
    layer_info: list[dict] = []

    for layer in layers:
        name = validate_name(layer["name"], "layer name")
        layer_type = layer["type"]
        thickness = validate_positive(float(layer["thickness_mm"]), "thickness_mm")
        material = layer["material"]

        z_bottom = z_current
        z_top = z_current + thickness

        # Determine component name based on layer type
        if layer_type == "dielectric":
            component = "PCB_Dielectric"
        elif layer_type in ("ground", "power"):
            component = "PCB_Planes"
        else:
            component = "PCB_Signal"

        vba = (
            VBABuilder("Brick")
            .call("Reset")
            .set("Name", name)
            .set("Component", component)
            .set("Material", material)
            .set_double("Xrange", -half_w, half_w)
            .set_double("Yrange", -half_l, half_l)
            .set_double("Zrange", z_bottom, z_top)
            .call("Create")
        )
        script.add_block(vba)

        layer_info.append({
            "name": name,
            "type": layer_type,
            "material": material,
            "z_bottom": round(z_bottom, 6),
            "z_top": round(z_top, 6),
            "thickness_mm": thickness,
        })

        z_current = z_top

    total_thickness = round(z_current, 6)
    vba_code = script.build()
    result = client.execute_vba(vba_code)
    result["total_thickness_mm"] = total_thickness
    result["layer_count"] = len(layers)
    result["layers"] = layer_info
    result["board_dimensions"] = {
        "width_mm": board_w,
        "length_mm": board_l,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_create_trace(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Create a PCB trace with optional impedance-based width calculation."""
    trace_type = arguments["trace_type"]
    length_mm = validate_positive(float(arguments["length_mm"]), "length_mm")
    layer = validate_name(arguments["layer"], "layer")
    x_start = float(arguments.get("x_start", 0))
    y_start = float(arguments.get("y_start", 0))
    direction = str(arguments.get("direction", "x"))
    copper_t = float(arguments.get("copper_thickness_mm", 0.035))
    z_pos = float(arguments.get("z_position", 0))

    impedance_target = arguments.get("impedance_target")
    width_mm = arguments.get("width_mm")
    impedance_info: dict = {}

    if impedance_target is not None:
        z_target = validate_positive(float(impedance_target), "impedance_target")
        sub_h = validate_positive(
            float(arguments["substrate_height_mm"]), "substrate_height_mm"
        )
        er = validate_positive(float(arguments["epsilon_r"]), "epsilon_r")

        if trace_type == "microstrip":
            width_mm = _microstrip_width_for_impedance(z_target, sub_h, er, copper_t)
            z_actual = _microstrip_impedance(width_mm, sub_h, er, copper_t)
        elif trace_type == "stripline":
            width_mm = _stripline_width_for_impedance(z_target, sub_h, er, copper_t)
            z_actual = _stripline_impedance(width_mm, sub_h, er, copper_t)
        elif trace_type in ("coplanar_waveguide", "grounded_cpw"):
            # For CPW, fall back to microstrip approximation with a note
            width_mm = _microstrip_width_for_impedance(z_target, sub_h, er, copper_t)
            z_actual = _microstrip_impedance(width_mm, sub_h, er, copper_t)
            impedance_info["note"] = (
                "CPW impedance depends on gap width (not modeled here). "
                "The width is estimated using microstrip formulas as a starting point. "
                "Use a 2D field solver for accurate CPW impedance."
            )
        else:
            width_mm = _microstrip_width_for_impedance(z_target, sub_h, er, copper_t)
            z_actual = _microstrip_impedance(width_mm, sub_h, er, copper_t)

        impedance_info.update({
            "target_ohms": z_target,
            "calculated_ohms": round(z_actual, 2),
            "calculated_width_mm": round(width_mm, 4),
            "substrate_height_mm": sub_h,
            "epsilon_r": er,
            "formula": "Hammerstad-Jensen" if trace_type == "microstrip" else "Cohn",
        })
    elif width_mm is None:
        return [TextContent(type="text", text=json.dumps({
            "status": "error",
            "message": (
                "Either width_mm or impedance_target must be specified. "
                "Provide width_mm for a fixed-width trace, or impedance_target "
                "with substrate_height_mm and epsilon_r for auto-calculation."
            ),
        }))]

    width_mm = validate_positive(float(width_mm), "width_mm")

    # Calculate trace end coordinates based on direction
    if direction.lower() == "x":
        angle_rad = 0.0
    elif direction.lower() == "y":
        angle_rad = math.pi / 2.0
    else:
        try:
            angle_rad = math.radians(float(direction))
        except ValueError:
            return [TextContent(type="text", text=json.dumps({
                "status": "error",
                "message": f"Invalid direction '{direction}'. Use 'x', 'y', or angle in degrees.",
            }))]

    dx = length_mm * math.cos(angle_rad)
    dy = length_mm * math.sin(angle_rad)

    # The trace is a rectangle along the direction vector
    # Calculate perpendicular offset for width
    perp_x = -math.sin(angle_rad) * width_mm / 2.0
    perp_y = math.cos(angle_rad) * width_mm / 2.0

    # Four corners of the trace polygon
    x0, y0 = x_start + perp_x, y_start + perp_y
    x1, y1 = x_start - perp_x, y_start - perp_y
    x2, y2 = x_start + dx - perp_x, y_start + dy - perp_y
    x3, y3 = x_start + dx + perp_x, y_start + dy + perp_y

    trace_name = f"Trace_{layer}"
    component = "PCB_Traces"

    if abs(angle_rad) < 1e-9 or abs(angle_rad - math.pi / 2.0) < 1e-9:
        # Axis-aligned trace: use a simple brick for efficiency
        if abs(angle_rad) < 1e-9:
            # X-direction
            x_min = x_start
            x_max = x_start + length_mm
            y_min = y_start - width_mm / 2.0
            y_max = y_start + width_mm / 2.0
        else:
            # Y-direction
            x_min = x_start - width_mm / 2.0
            x_max = x_start + width_mm / 2.0
            y_min = y_start
            y_max = y_start + length_mm

        vba = (
            VBABuilder("Brick")
            .call("Reset")
            .set("Name", trace_name)
            .set("Component", component)
            .set("Material", "Copper (annealed)")
            .set_double("Xrange", x_min, x_max)
            .set_double("Yrange", y_min, y_max)
            .set_double("Zrange", z_pos, z_pos + copper_t)
            .call("Create")
        )
        vba_code = vba.build()
    else:
        # Angled trace: use extrude with polygon profile
        script = VBAScript()
        script.add_comment(f"Angled PCB trace on {layer}")

        poly_vba = (
            VBABuilder("Polygon")
            .call("Reset")
            .set("Name", f"{trace_name}_profile")
            .set("Curve", f"{trace_name}_curves")
        )
        for px, py in [(x0, y0), (x1, y1), (x2, y2), (x3, y3), (x0, y0)]:
            poly_vba.set_double("Point", px, py)
        poly_vba.call("Create")
        script.add_block(poly_vba)

        extrude_vba = (
            VBABuilder("ExtrudeCurve")
            .call("Reset")
            .set("Name", trace_name)
            .set("Component", component)
            .set("Material", "Copper (annealed)")
            .set_number("Thickness", copper_t)
            .set_double("Twistangle", 0, 0)
            .set_double("Taperangle", 0, 0)
            .set("Curve", f"{trace_name}_curves:{trace_name}_profile")
            .set("Axis", "z")
            .call("Create")
        )
        script.add_block(extrude_vba)
        vba_code = script.build()

    result = client.execute_vba(vba_code)
    result["trace_type"] = trace_type
    result["layer"] = layer
    result["width_mm"] = round(width_mm, 4)
    result["length_mm"] = length_mm
    result["direction_deg"] = round(math.degrees(angle_rad), 2)
    result["start"] = {"x": x_start, "y": y_start, "z": z_pos}
    result["end"] = {
        "x": round(x_start + dx, 4),
        "y": round(y_start + dy, 4),
        "z": z_pos,
    }
    if impedance_info:
        result["impedance"] = impedance_info

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_create_via(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Create a PCB via with barrel, pads, and antipads."""
    x = float(arguments["x"])
    y = float(arguments["y"])
    drill_d = float(arguments.get("drill_diameter_mm", 0.3))
    pad_d = float(arguments.get("pad_diameter_mm", 0.6))
    antipad_d = float(arguments.get("antipad_diameter_mm", 0.8))
    start_layer = validate_name(arguments["start_layer"], "start_layer")
    end_layer = validate_name(arguments["end_layer"], "end_layer")
    start_z = float(arguments["start_z"])
    end_z = float(arguments["end_z"])
    via_type = arguments.get("via_type", "through")
    via_name = arguments.get("name", f"Via_{start_layer}_{end_layer}")
    via_name = validate_name(via_name, "via name")

    validate_positive(drill_d, "drill_diameter_mm")
    validate_positive(pad_d, "pad_diameter_mm")
    validate_positive(antipad_d, "antipad_diameter_mm")

    if pad_d <= drill_d:
        return [TextContent(type="text", text=json.dumps({
            "status": "error",
            "message": (
                f"pad_diameter_mm ({pad_d}) must be larger than "
                f"drill_diameter_mm ({drill_d})"
            ),
        }))]

    if antipad_d <= pad_d:
        return [TextContent(type="text", text=json.dumps({
            "status": "error",
            "message": (
                f"antipad_diameter_mm ({antipad_d}) must be larger than "
                f"pad_diameter_mm ({pad_d})"
            ),
        }))]

    # Ensure start_z > end_z (top to bottom)
    z_top = max(start_z, end_z)
    z_bot = min(start_z, end_z)

    drill_r = drill_d / 2.0
    pad_r = pad_d / 2.0

    script = VBAScript()
    script.add_comment(f"PCB Via: {via_name} ({via_type})")

    # Via barrel (hollow cylinder: outer = pad, inner = drill)
    barrel_vba = (
        VBABuilder("Cylinder")
        .call("Reset")
        .set("Name", f"{via_name}_barrel")
        .set("Component", "PCB_Vias")
        .set("Material", "Copper (annealed)")
        .set("Axis", "z")
        .set_number("Outerradius", pad_r)
        .set_number("Innerradius", drill_r)
        .set_number("Xcenter", x)
        .set_number("Ycenter", y)
        .set_number("Zcenter", 0)
        .set_double("Zrange", z_bot, z_top)
        .call("Create")
    )
    script.add_block(barrel_vba)

    vba_code = script.build()
    result = client.execute_vba(vba_code)
    result["via_name"] = via_name
    result["via_type"] = via_type
    result["position"] = {"x": x, "y": y}
    result["z_range"] = {"top": z_top, "bottom": z_bot}
    result["dimensions"] = {
        "drill_diameter_mm": drill_d,
        "pad_diameter_mm": pad_d,
        "antipad_diameter_mm": antipad_d,
        "annular_ring_mm": round((pad_d - drill_d) / 2.0, 4),
    }
    result["layers"] = {
        "start": start_layer,
        "end": end_layer,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_create_ground_plane(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Create a ground/power plane with optional cutouts."""
    layer = validate_name(arguments["layer"], "layer")
    width_mm = validate_positive(float(arguments["width_mm"]), "width_mm")
    length_mm = validate_positive(float(arguments["length_mm"]), "length_mm")
    z_pos = float(arguments.get("z_position", 0))
    thickness = float(arguments.get("thickness_mm", 0.035))
    cutouts: list[dict] = arguments.get("cutouts", [])

    half_w = width_mm / 2.0
    half_l = length_mm / 2.0
    plane_name = f"Plane_{layer}"

    script = VBAScript()
    script.add_comment(f"PCB Plane: {layer}")

    # Main plane brick
    plane_vba = (
        VBABuilder("Brick")
        .call("Reset")
        .set("Name", plane_name)
        .set("Component", "PCB_Planes")
        .set("Material", "Copper (annealed)")
        .set_double("Xrange", -half_w, half_w)
        .set_double("Yrange", -half_l, half_l)
        .set_double("Zrange", z_pos, z_pos + thickness)
        .call("Create")
    )
    script.add_block(plane_vba)

    # Subtract cutouts
    cutout_info: list[dict] = []
    for i, cutout in enumerate(cutouts):
        cutout_name = f"{plane_name}_cutout{i}"
        x_min = float(cutout["x_min"])
        x_max = float(cutout["x_max"])
        y_min = float(cutout["y_min"])
        y_max = float(cutout["y_max"])

        # Create the cutout brick
        cutout_vba = (
            VBABuilder("Brick")
            .call("Reset")
            .set("Name", cutout_name)
            .set("Component", "PCB_Planes")
            .set("Material", "Copper (annealed)")
            .set_double("Xrange", x_min, x_max)
            .set_double("Yrange", y_min, y_max)
            .set_double("Zrange", z_pos, z_pos + thickness)
            .call("Create")
        )
        script.add_block(cutout_vba)

        # Boolean subtract the cutout from the plane
        subtract_vba = VBABuilder("Solid")
        subtract_vba.raw_line(
            f'Solid.Subtract "PCB_Planes:{plane_name}", '
            f'"PCB_Planes:{cutout_name}"'
        )
        script.add_block(subtract_vba)

        cutout_info.append({
            "index": i,
            "x_range": [x_min, x_max],
            "y_range": [y_min, y_max],
        })

    vba_code = script.build()
    result = client.execute_vba(vba_code)
    result["layer"] = layer
    result["dimensions"] = {
        "width_mm": width_mm,
        "length_mm": length_mm,
        "thickness_mm": thickness,
    }
    result["z_position"] = z_pos
    result["cutout_count"] = len(cutouts)
    if cutout_info:
        result["cutouts"] = cutout_info

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_import_gerber(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Import a Gerber file into CST Studio."""
    file_path = arguments["file_path"]
    layer_name = validate_name(arguments["layer_name"], "layer_name")
    file_type = arguments.get("file_type", "gerber")

    if file_type == "gerber":
        # CST VBA Gerber import
        vba = (
            VBABuilder("EDAImport")
            .call("Reset")
            .set("FileName", file_path)
            .set("ImportAs", "2D")
            .set("LayerName", layer_name)
            .set("Type", "Gerber")
            .set_bool("UseUnits", True)
            .set("Units", "mm")
            .call("Import")
        )
    elif file_type == "odb++":
        vba = (
            VBABuilder("EDAImport")
            .call("Reset")
            .set("FileName", file_path)
            .set("ImportAs", "3D")
            .set("LayerName", layer_name)
            .set("Type", "ODB++")
            .set_bool("UseUnits", True)
            .set("Units", "mm")
            .call("Import")
        )
    elif file_type == "dxf":
        vba = (
            VBABuilder("DXF")
            .call("Reset")
            .set("FileName", file_path)
            .set("ScaleToUnit", "mm")
            .set_number("ImportLineWidth", 0)
            .set("AddAllShapes", "True")
            .call("Read")
        )
    else:
        return [TextContent(type="text", text=json.dumps({
            "status": "error",
            "message": f"Unsupported file type: {file_type}. Use 'gerber', 'odb++', or 'dxf'.",
        }))]

    vba_code = vba.build()
    result = client.execute_vba(vba_code)
    result["file_path"] = file_path
    result["layer_name"] = layer_name
    result["file_type"] = file_type

    if result.get("status") == "offline":
        result["import_notes"] = (
            "To import Gerber files in CST Studio:\n"
            "1. Open CST Studio Suite on Windows\n"
            "2. Use Modeling > Import > EDA/Layout to open the import wizard\n"
            "3. Select the Gerber file and assign it to the correct layer\n"
            "4. Set the units to mm and configure layer stackup\n"
            "5. Click Import to generate the 3D PCB model\n\n"
            "Alternatively, use the generated VBA script in the CST macro editor."
        )

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_list_stackup_templates(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    """List predefined PCB stackup templates from the data file."""
    stackup_file = DATA_DIR / "pcb_stackups.json"

    try:
        data = json.loads(stackup_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [TextContent(type="text", text=json.dumps({
            "status": "error",
            "message": f"Stackup templates file not found: {stackup_file}",
        }))]

    stackups = data.get("stackups", [])

    # Apply optional filter
    filter_str = arguments.get("filter", "")
    if filter_str:
        filter_lower = filter_str.lower()
        stackups = [
            s for s in stackups
            if filter_lower in s["name"].lower()
            or filter_lower in s.get("description", "").lower()
        ]

    result = {
        "status": "ok",
        "template_count": len(stackups),
        "templates": stackups,
        "usage_hint": (
            "Pass the 'layers' array from any template directly to "
            "cst_pcb_create_stackup along with board_width_mm and board_length_mm."
        ),
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def handle(
    name: str, arguments: dict, client: CSTClient
) -> list[TextContent]:
    """Handle a PCB tool call."""
    if name == "cst_pcb_create_stackup":
        return await _handle_create_stackup(arguments, client)
    if name == "cst_pcb_create_trace":
        return await _handle_create_trace(arguments, client)
    if name == "cst_pcb_create_via":
        return await _handle_create_via(arguments, client)
    if name == "cst_pcb_create_ground_plane":
        return await _handle_create_ground_plane(arguments, client)
    if name == "cst_pcb_import_gerber":
        return await _handle_import_gerber(arguments, client)
    if name == "cst_pcb_list_stackup_templates":
        return await _handle_list_stackup_templates(arguments, client)

    return [
        TextContent(
            type="text",
            text=json.dumps({"error": f"Unknown PCB tool: {name}"}),
        )
    ]


def register_pcb_tools(server: Server, client: CSTClient) -> None:
    """Register PCB tools with the MCP server."""
    from mcp_cst_studio.tools import _registry
    _registry.add_module(TOOLS, handle, client)
