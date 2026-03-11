"""Material management tools for CST Studio MCP server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.validators import validate_name, validate_non_negative, validate_range
from mcp_cst_studio.vba_builder import VBABuilder, VBAScript

if TYPE_CHECKING:
    from mcp.server import Server

DATA_DIR = Path(__file__).parent.parent / "data" / "materials"

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="cst_create_material",
        description=(
            "Create a new material with electromagnetic properties in CST Studio. "
            "Specify relative permittivity (epsilon), relative permeability (mu), "
            "electric and magnetic loss tangents, and conductivity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'MySubstrate')",
                },
                "epsilon": {
                    "type": "number",
                    "description": "Relative permittivity (epsilon_r)",
                    "default": 1.0,
                },
                "mu": {
                    "type": "number",
                    "description": "Relative permeability (mu_r)",
                    "default": 1.0,
                },
                "tan_d_e": {
                    "type": "number",
                    "description": "Electric loss tangent (tan delta_e)",
                    "default": 0.0,
                },
                "tan_d_m": {
                    "type": "number",
                    "description": "Magnetic loss tangent (tan delta_m)",
                    "default": 0.0,
                },
                "conductivity": {
                    "type": "number",
                    "description": "Electric conductivity in S/m",
                    "default": 0.0,
                },
                "color_r": {
                    "type": "number",
                    "description": "Red colour component (0-1)",
                    "default": 0.6,
                },
                "color_g": {
                    "type": "number",
                    "description": "Green colour component (0-1)",
                    "default": 0.6,
                },
                "color_b": {
                    "type": "number",
                    "description": "Blue colour component (0-1)",
                    "default": 0.6,
                },
                "transparency": {
                    "type": "number",
                    "description": "Transparency (0 = opaque, 1 = fully transparent)",
                    "default": 0.0,
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="cst_create_lossy_metal",
        description=(
            "Create a lossy metal material in CST Studio. Lossy metals model "
            "finite conductivity skin-effect losses, essential for accurate loss "
            "calculations in connectors, waveguides, and PCB traces."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Lossy Copper')",
                },
                "conductivity": {
                    "type": "number",
                    "description": "Electric conductivity in S/m (e.g. 5.8e7 for copper)",
                },
                "mu": {
                    "type": "number",
                    "description": "Relative permeability (mu_r)",
                    "default": 1.0,
                },
            },
            "required": ["name", "conductivity"],
        },
    ),
    Tool(
        name="cst_create_anisotropic_material",
        description=(
            "Create an anisotropic material with per-axis permittivity, "
            "permeability, and loss tangent values. Used for crystals, "
            "metamaterials, and composite substrates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Material name"},
                "epsilon_x": {"type": "number", "description": "Relative permittivity along X"},
                "epsilon_y": {"type": "number", "description": "Relative permittivity along Y"},
                "epsilon_z": {"type": "number", "description": "Relative permittivity along Z"},
                "mu_x": {"type": "number", "description": "Relative permeability along X"},
                "mu_y": {"type": "number", "description": "Relative permeability along Y"},
                "mu_z": {"type": "number", "description": "Relative permeability along Z"},
                "tan_d_x": {
                    "type": "number",
                    "description": "Electric loss tangent along X",
                    "default": 0.0,
                },
                "tan_d_y": {
                    "type": "number",
                    "description": "Electric loss tangent along Y",
                    "default": 0.0,
                },
                "tan_d_z": {
                    "type": "number",
                    "description": "Electric loss tangent along Z",
                    "default": 0.0,
                },
            },
            "required": [
                "name",
                "epsilon_x", "epsilon_y", "epsilon_z",
                "mu_x", "mu_y", "mu_z",
            ],
        },
    ),
    Tool(
        name="cst_load_material",
        description=(
            "Load a material from the CST material library by its library name. "
            "The material is added to the project under the given name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the material in the project",
                },
                "library_name": {
                    "type": "string",
                    "description": "Name in the CST library (e.g. 'Copper (annealed)')",
                },
            },
            "required": ["name", "library_name"],
        },
    ),
    Tool(
        name="cst_list_materials",
        description=(
            "List available materials from the bundled material database. "
            "Optionally filter by category: 'metals', 'dielectrics', or 'substrates'. "
            "Returns name, key EM properties, and usage notes for each material."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category: 'metals', 'dielectrics', or 'substrates'",
                    "enum": ["metals", "dielectrics", "substrates"],
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_assign_material",
        description=(
            "Assign a material to an existing solid in CST Studio. "
            "The solid is specified as 'Component:SolidName'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "solid": {
                    "type": "string",
                    "description": "Solid reference as 'Component:SolidName' (e.g. 'Antenna:Patch')",
                },
                "material": {
                    "type": "string",
                    "description": "Material name to assign",
                },
            },
            "required": ["solid", "material"],
        },
    ),
    Tool(
        name="cst_get_material_info",
        description=(
            "Get electromagnetic properties of a material from the bundled "
            "database. Returns epsilon_r, mu_r, conductivity, loss tangent, "
            "and usage notes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Copper', 'FR-4', 'Rogers RO4003C')",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="cst_delete_material",
        description="Delete a material from the current CST project.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the material to delete",
                },
            },
            "required": ["name"],
        },
    ),
]

# ---------------------------------------------------------------------------
# Bundled material database helpers
# ---------------------------------------------------------------------------

_material_db_cache: dict[str, list[dict]] | None = None


def _load_material_db() -> dict[str, list[dict]]:
    """Load and cache the bundled JSON material databases."""
    global _material_db_cache  # noqa: PLW0603
    if _material_db_cache is not None:
        return _material_db_cache

    db: dict[str, list[dict]] = {"metals": [], "dielectrics": []}

    metals_path = DATA_DIR / "common_metals.json"
    if metals_path.exists():
        with metals_path.open() as f:
            data = json.load(f)
        db["metals"] = data.get("metals", [])

    dielectrics_path = DATA_DIR / "common_dielectrics.json"
    if dielectrics_path.exists():
        with dielectrics_path.open() as f:
            data = json.load(f)
        db["dielectrics"] = data.get("dielectrics", [])

    substrates_path = DATA_DIR / "substrates.json"
    if substrates_path.exists():
        with substrates_path.open() as f:
            db["substrates"] = json.load(f)

    _material_db_cache = db
    return db


def _find_material(name: str) -> dict | None:
    """Find a material by name in the bundled database (case-insensitive)."""
    db = _load_material_db()
    name_lower = name.lower()
    for category in db.values():
        for mat in category:
            if mat["name"].lower() == name_lower:
                return mat
    return None


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Dispatch a material tool call and return results."""
    try:
        if name == "cst_create_material":
            return _handle_create_material(arguments, client)

        if name == "cst_create_lossy_metal":
            return _handle_create_lossy_metal(arguments, client)

        if name == "cst_create_anisotropic_material":
            return _handle_create_anisotropic_material(arguments, client)

        if name == "cst_load_material":
            return _handle_load_material(arguments, client)

        if name == "cst_list_materials":
            return _handle_list_materials(arguments)

        if name == "cst_assign_material":
            return _handle_assign_material(arguments, client)

        if name == "cst_get_material_info":
            return _handle_get_material_info(arguments)

        if name == "cst_delete_material":
            return _handle_delete_material(arguments, client)

        return [TextContent(type="text", text=json.dumps({
            "status": "error", "message": f"Unknown material tool: {name}",
        }))]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({
            "status": "error", "message": str(e),
        }))]


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------

def _handle_create_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon = float(args.get("epsilon", 1.0))
    mu = float(args.get("mu", 1.0))
    tan_d_e = float(args.get("tan_d_e", 0.0))
    tan_d_m = float(args.get("tan_d_m", 0.0))
    conductivity = float(args.get("conductivity", 0.0))
    color_r = validate_range(float(args.get("color_r", 0.6)), 0.0, 1.0, "color_r")
    color_g = validate_range(float(args.get("color_g", 0.6)), 0.0, 1.0, "color_g")
    color_b = validate_range(float(args.get("color_b", 0.6)), 0.0, 1.0, "color_b")
    transparency = validate_range(float(args.get("transparency", 0.0)), 0.0, 1.0, "transparency")
    validate_non_negative(conductivity, "conductivity")
    validate_non_negative(tan_d_e, "tan_d_e")
    validate_non_negative(tan_d_m, "tan_d_m")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set_number("Epsilon", epsilon)
        .set_number("Mu", mu)
        .set_number("TanDe", tan_d_e)
        .set_number("TanDm", tan_d_m)
        .set_number("Sigma", conductivity)
        .set_triple("Colour", color_r, color_g, color_b)
        .set_number("Transparency", transparency)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_create_material",
            "material": mat_name,
            "properties": {
                "epsilon_r": epsilon,
                "mu_r": mu,
                "tan_d_e": tan_d_e,
                "tan_d_m": tan_d_m,
                "conductivity_S_m": conductivity,
            },
            "vba": vba,
            **result,
        }, indent=2),
    )]


def _handle_create_lossy_metal(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    conductivity = float(args["conductivity"])
    mu = float(args.get("mu", 1.0))
    validate_non_negative(conductivity, "conductivity")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Lossy metal")
        .set_number("Sigma", conductivity)
        .set_number("Mu", mu)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_create_lossy_metal",
            "material": mat_name,
            "properties": {
                "type": "Lossy metal",
                "conductivity_S_m": conductivity,
                "mu_r": mu,
            },
            "vba": vba,
            **result,
        }, indent=2),
    )]


def _handle_create_anisotropic_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    eps_x = float(args["epsilon_x"])
    eps_y = float(args["epsilon_y"])
    eps_z = float(args["epsilon_z"])
    mu_x = float(args["mu_x"])
    mu_y = float(args["mu_y"])
    mu_z = float(args["mu_z"])
    td_x = float(args.get("tan_d_x", 0.0))
    td_y = float(args.get("tan_d_y", 0.0))
    td_z = float(args.get("tan_d_z", 0.0))
    validate_non_negative(td_x, "tan_d_x")
    validate_non_negative(td_y, "tan_d_y")
    validate_non_negative(td_z, "tan_d_z")

    script = VBAScript()
    script.add_comment(f"Create anisotropic material: {mat_name}")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Anisotropic")
        .set_number("EpsilonX", eps_x)
        .set_number("EpsilonY", eps_y)
        .set_number("EpsilonZ", eps_z)
        .set_number("MuX", mu_x)
        .set_number("MuY", mu_y)
        .set_number("MuZ", mu_z)
        .set_number("TanDeX", td_x)
        .set_number("TanDeY", td_y)
        .set_number("TanDeZ", td_z)
        .call("Create")
    )
    script.add_block(vba)
    code = script.build()

    result = client.execute_vba(code)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_create_anisotropic_material",
            "material": mat_name,
            "properties": {
                "type": "Anisotropic",
                "epsilon": {"x": eps_x, "y": eps_y, "z": eps_z},
                "mu": {"x": mu_x, "y": mu_y, "z": mu_z},
                "tan_d": {"x": td_x, "y": td_y, "z": td_z},
            },
            "vba": code,
            **result,
        }, indent=2),
    )]


def _handle_load_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    library_name = args["library_name"]

    vba = (
        VBABuilder("Material")
        .call_with_args("LoadMaterial", library_name, mat_name)
        .build()
    )

    result = client.execute_vba(vba)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_load_material",
            "material": mat_name,
            "library_name": library_name,
            "vba": vba,
            **result,
        }, indent=2),
    )]


def _handle_list_materials(args: dict) -> list[TextContent]:
    db = _load_material_db()
    category = args.get("category")

    if category == "metals":
        materials = db.get("metals", [])
    elif category == "dielectrics":
        materials = db.get("dielectrics", [])
    elif category == "substrates":
        materials = db.get("substrates", [])
    else:
        # Return all categories
        materials = []
        for cat_name, cat_list in db.items():
            for mat in cat_list:
                materials.append({**mat, "category": cat_name})

    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_list_materials",
            "category": category or "all",
            "count": len(materials),
            "materials": materials,
        }, indent=2),
    )]


def _handle_assign_material(args: dict, client: CSTClient) -> list[TextContent]:
    solid = args["solid"]
    material = validate_name(args["material"], "material name")

    # Validate the solid reference format (Component:SolidName)
    if ":" not in solid:
        raise ValueError(
            f"Invalid solid reference '{solid}': expected 'Component:SolidName' format"
        )
    component, solid_name = solid.split(":", 1)
    validate_name(component, "component")
    validate_name(solid_name, "solid")

    vba = (
        VBABuilder("Solid")
        .call_with_args("SetMaterial", f"{component}:{solid_name}", material)
        .build()
    )

    result = client.execute_vba(vba)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_assign_material",
            "solid": f"{component}:{solid_name}",
            "material": material,
            "vba": vba,
            **result,
        }, indent=2),
    )]


def _handle_get_material_info(args: dict) -> list[TextContent]:
    name = args["name"]
    mat = _find_material(name)

    if mat is None:
        # List available names to help the user
        db = _load_material_db()
        available = []
        for cat_list in db.values():
            available.extend(m["name"] for m in cat_list)
        return [TextContent(
            type="text",
            text=json.dumps({
                "tool": "cst_get_material_info",
                "status": "error",
                "message": f"Material '{name}' not found in bundled database",
                "available_materials": sorted(available),
            }, indent=2),
        )]

    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_get_material_info",
            "material": mat,
        }, indent=2),
    )]


def _handle_delete_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")

    vba = (
        VBABuilder("Material")
        .call_with_args("Delete", mat_name)
        .build()
    )

    result = client.execute_vba(vba)
    return [TextContent(
        type="text",
        text=json.dumps({
            "tool": "cst_delete_material",
            "material": mat_name,
            "vba": vba,
            **result,
        }, indent=2),
    )]


# ---------------------------------------------------------------------------
# Registration helper (used by tools/__init__.py)
# ---------------------------------------------------------------------------

def register_material_tools(server: Server, client: CSTClient) -> None:
    """Register material tools with the MCP server.

    Appends tool definitions and the ``handle`` dispatcher to the
    shared ``ToolRegistry`` in ``mcp_cst_studio.tools``.  The registry
    is wired into the MCP protocol by ``register_all_tools`` after all
    modules have been registered.
    """
    from mcp_cst_studio.tools import _registry

    _registry.add_module(TOOLS, handle, client)
