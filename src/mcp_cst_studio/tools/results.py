"""Result extraction tools for CST Studio MCP server.

Provides 10 tools for extracting and exporting simulation results including
S-parameters, far-field patterns, impedance, VSWR, gain, efficiency, and
general result tree navigation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.types import FieldMonitorType
from mcp_cst_studio.validators import validate_file_path, validate_frequency, validate_port_number
from mcp_cst_studio.vba_builder import VBABuilder, VBAScript

if TYPE_CHECKING:
    from mcp.server import Server

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    # 1. cst_get_s_parameters
    Tool(
        name="cst_get_s_parameters",
        description=(
            "Extract S-parameter results from a completed CST simulation. "
            "Returns S-parameter data (magnitude, phase, real/imaginary) for "
            "the specified port pair. In connected mode reads directly from "
            "the result tree; in offline mode returns VBA scripts and explains "
            "the CST result tree structure."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_in": {
                    "type": "integer",
                    "description": "Input port number (excitation port).",
                    "default": 1,
                },
                "port_out": {
                    "type": "integer",
                    "description": "Output port number (observation port).",
                    "default": 1,
                },
                "format": {
                    "type": "string",
                    "description": (
                        "Output format for S-parameter data. "
                        "'db' = magnitude in dB, 'mag' = linear magnitude, "
                        "'real_imag' = real and imaginary parts, "
                        "'phase' = phase in degrees."
                    ),
                    "default": "db",
                    "enum": ["db", "mag", "real_imag", "phase"],
                },
            },
            "required": [],
        },
    ),
    # 2. cst_get_farfield
    Tool(
        name="cst_get_farfield",
        description=(
            "Get far-field radiation pattern results from a completed CST "
            "simulation at a specific frequency. Returns gain, directivity, "
            "radiation efficiency, and beam widths. Requires a farfield "
            "monitor at the specified frequency."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "frequency": {
                    "type": "number",
                    "description": "Frequency in GHz at which to extract the far-field pattern.",
                },
                "monitor_name": {
                    "type": "string",
                    "description": (
                        "Name of the far-field monitor. If omitted, defaults to "
                        "'farfield (f=<frequency>)' which is the CST auto-generated name."
                    ),
                },
            },
            "required": ["frequency"],
        },
    ),
    # 3. cst_add_field_monitor
    Tool(
        name="cst_add_field_monitor",
        description=(
            "Add a field monitor at a specific frequency to the CST project. "
            "Field monitors must be defined before running a simulation to "
            "capture field distributions, far-field patterns, surface currents, "
            "or power flow at the desired frequency."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "monitor_type": {
                    "type": "string",
                    "description": (
                        "Type of field monitor to add. Options: "
                        "Efield (electric field), Hfield (magnetic field), "
                        "Powerflow (Poynting vector), Current (volume current), "
                        "Powerloss (loss density), Farfield (radiation pattern), "
                        "Surfacecurrent (surface current density)."
                    ),
                    "enum": [e.value for e in FieldMonitorType],
                },
                "frequency": {
                    "type": "number",
                    "description": "Monitor frequency in GHz.",
                },
                "name": {
                    "type": "string",
                    "description": (
                        "Custom name for the monitor. If omitted, a name is "
                        "auto-generated from the type and frequency, e.g. "
                        "'e-field (f=2.45)'."
                    ),
                },
            },
            "required": ["monitor_type", "frequency"],
        },
    ),
    # 4. cst_get_impedance
    Tool(
        name="cst_get_impedance",
        description=(
            "Get input impedance (Z-parameters) for a port from a completed "
            "CST simulation. Returns real and imaginary impedance vs frequency. "
            "Useful for matching network design and feed optimization."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "Port number to extract impedance for.",
                    "default": 1,
                },
            },
            "required": [],
        },
    ),
    # 5. cst_get_vswr
    Tool(
        name="cst_get_vswr",
        description=(
            "Get Voltage Standing Wave Ratio (VSWR) for a port from a "
            "completed CST simulation. VSWR indicates impedance matching "
            "quality: 1.0 is perfect match, <2.0 is generally acceptable. "
            "Can also be computed from S11: VSWR = (1+|S11|)/(1-|S11|)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "description": "Port number to extract VSWR for.",
                    "default": 1,
                },
            },
            "required": [],
        },
    ),
    # 6. cst_get_gain
    Tool(
        name="cst_get_gain",
        description=(
            "Get antenna gain at a specific frequency from a completed CST "
            "simulation. Returns peak gain in dBi and the direction (theta, "
            "phi) of maximum gain. Requires a farfield monitor at the "
            "specified frequency."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "frequency": {
                    "type": "number",
                    "description": "Frequency in GHz at which to extract gain.",
                },
            },
            "required": ["frequency"],
        },
    ),
    # 7. cst_get_efficiency
    Tool(
        name="cst_get_efficiency",
        description=(
            "Get antenna radiation efficiency from a completed CST simulation "
            "at a specific frequency. Returns total efficiency (including "
            "mismatch), radiation efficiency (excluding mismatch), and "
            "mismatch loss in dB."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "frequency": {
                    "type": "number",
                    "description": "Frequency in GHz at which to extract efficiency.",
                },
            },
            "required": ["frequency"],
        },
    ),
    # 8. cst_list_results
    Tool(
        name="cst_list_results",
        description=(
            "List all available results in the CST result tree. Optionally "
            "specify a subtree path to narrow the listing. Useful for "
            "discovering what simulation results are available before "
            "extracting specific data."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "tree_path": {
                    "type": "string",
                    "description": (
                        "Result tree path to list, e.g. '1D Results', "
                        "'1D Results\\S-Parameters', 'Farfields', "
                        "'2D/3D Results'. Omit to list top-level result categories."
                    ),
                },
            },
            "required": [],
        },
    ),
    # 9. cst_export_result
    Tool(
        name="cst_export_result",
        description=(
            "Export a simulation result to a file (CSV, Touchstone, or text). "
            "Specify the result tree path and desired output format. Useful "
            "for post-processing results in external tools like MATLAB or Python."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "result_path": {
                    "type": "string",
                    "description": (
                        "CST result tree path to export, e.g. "
                        "'1D Results\\S-Parameters\\S1,1' or "
                        "'Farfields\\farfield (f=2.45)'."
                    ),
                },
                "output_file": {
                    "type": "string",
                    "description": "Full file path for the exported file.",
                },
                "format": {
                    "type": "string",
                    "description": (
                        "Export file format. 'csv' for comma-separated values, "
                        "'touchstone' for Touchstone/SnP format (S-parameters only), "
                        "'txt' for space-separated text."
                    ),
                    "default": "csv",
                    "enum": ["csv", "touchstone", "txt"],
                },
            },
            "required": ["result_path", "output_file"],
        },
    ),
    # 10. cst_get_result_summary
    Tool(
        name="cst_get_result_summary",
        description=(
            "Get a summary of all key simulation results from a completed CST "
            "simulation. Returns an overview of S-parameters, gain, efficiency, "
            "and impedance. Useful for a quick design evaluation without "
            "querying each result type individually."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(data: dict) -> list[TextContent]:
    """Wrap a dict as a single JSON TextContent response."""
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


def _s_param_tree_path(port_out: int, port_in: int) -> str:
    """Build the CST result tree path for an S-parameter.

    CST convention: ``1D Results\\S-Parameters\\SX,Y`` where X is the
    output port and Y is the input port.
    """
    return f"1D Results\\S-Parameters\\S{port_out},{port_in}"


def _farfield_tree_path(frequency: float, monitor_name: str | None = None) -> str:
    """Build the CST result tree path for a far-field result."""
    if monitor_name:
        return f"Farfields\\{monitor_name}"
    return f"Farfields\\farfield (f={frequency})"


def _impedance_tree_path(port: int) -> str:
    """Build the CST result tree path for Z-parameters."""
    return f"1D Results\\Z-Parameters\\Z{port},{port}"


def _vswr_tree_path(port: int) -> str:
    """Build the CST result tree path for VSWR."""
    return f"1D Results\\VSWR\\VSWR{port}"


# ---------------------------------------------------------------------------
# VBA script builders
# ---------------------------------------------------------------------------

def _build_s_parameter_vba(port_out: int, port_in: int, fmt: str) -> str:
    """Build VBA script for extracting S-parameters."""
    tree_path = _s_param_tree_path(port_out, port_in)
    script = VBAScript()
    script.add_comment(f"Extract S-parameter S{port_out},{port_in} in {fmt} format")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{tree_path}"',
        "",
        "  ' Access the 1D result data",
        "  Dim nPoints As Long",
        '  nPoints = Result1D("").GetN',
        "",
        "  Dim i As Long",
        "  For i = 0 To nPoints - 1",
        '    Dim freq As Double',
        '    freq = Result1D("").GetX(i)',
    ]

    if fmt == "db":
        lines += [
            '    Dim sVal As Double',
            '    sVal = Result1D("").GetY(i)  \' Already in dB for S-param results',
            '    Debug.Print freq & "," & sVal',
        ]
    elif fmt == "mag":
        lines += [
            '    Dim sDb As Double',
            '    sDb = Result1D("").GetY(i)',
            '    Dim sMag As Double',
            '    sMag = 10^(sDb / 20.0)',
            '    Debug.Print freq & "," & sMag',
        ]
    elif fmt == "phase":
        lines += [
            "    ' Select the phase result tree item",
            f'    SelectTreeItem "1D Results\\S-Parameters\\S{port_out},{port_in}_phase"',
            '    Dim sPhase As Double',
            '    sPhase = Result1D("").GetY(i)',
            '    Debug.Print freq & "," & sPhase',
        ]
    elif fmt == "real_imag":
        lines += [
            "    ' Read real and imaginary components",
            '    Dim sReal As Double, sImag As Double',
            f'    SelectTreeItem "1D Results\\S-Parameters\\S{port_out},{port_in}_real"',
            '    sReal = Result1D("").GetY(i)',
            f'    SelectTreeItem "1D Results\\S-Parameters\\S{port_out},{port_in}_imag"',
            '    sImag = Result1D("").GetY(i)',
            '    Debug.Print freq & "," & sReal & "," & sImag',
        ]

    lines += [
        "  Next i",
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_farfield_vba(frequency: float, monitor_name: str | None) -> str:
    """Build VBA script for extracting far-field results."""
    tree_path = _farfield_tree_path(frequency, monitor_name)
    script = VBAScript()
    script.add_comment(f"Extract far-field results at {frequency} GHz")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{tree_path}"',
        "",
        "  ' Access far-field result object",
        "  Dim ff As Object",
        f'  Set ff = FarfieldPlot',
        "",
        "  ' Read key far-field metrics",
        "  ff.Reset",
        f'  ff.Plottype "3D"',
        f'  ff.SetPlotMode "Gain"',
        "",
        "  ' Peak gain and direction",
        "  Dim peakGain As Double",
        "  peakGain = ff.GetMainLobeDirection",
        "",
        "  ' Beam widths",
        "  Dim bwE As Double, bwH As Double",
        f'  ff.SetPlotMode "Gain"',
        "",
        "  ' Print summary",
        '  Debug.Print "Peak Gain (dBi): " & ff.GetResultValue("max gain")',
        '  Debug.Print "Directivity (dBi): " & ff.GetResultValue("directivity")',
        '  Debug.Print "Efficiency: " & ff.GetResultValue("rad. efficiency")',
        '  Debug.Print "3dB Beam Width E-plane: " & ff.GetResultValue("angular width (3db), theta")',
        '  Debug.Print "3dB Beam Width H-plane: " & ff.GetResultValue("angular width (3db), phi")',
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_add_monitor_vba(
    monitor_type: str, frequency: float, name: str | None
) -> str:
    """Build VBA script for adding a field monitor."""
    if name is None:
        type_prefix_map = {
            "Efield": "e-field",
            "Hfield": "h-field",
            "Powerflow": "power",
            "Current": "current",
            "Powerloss": "loss",
            "Farfield": "farfield",
            "Surfacecurrent": "surface-current",
            "Eenergy": "e-energy",
            "Henergy": "h-energy",
        }
        prefix = type_prefix_map.get(monitor_type, monitor_type.lower())
        name = f"{prefix} (f={frequency})"

    script = VBAScript()
    script.add_comment(f"Add {monitor_type} field monitor '{name}' at {frequency} GHz")
    script.add_blank()

    builder = (
        VBABuilder("Monitor")
        .call("Reset")
        .set("Name", name)
        .set("FieldType", monitor_type)
        .set_number("Frequency", frequency)
        .call("Create")
    )
    script.add_block(builder)
    return script.build()


def _build_impedance_vba(port: int) -> str:
    """Build VBA script for extracting input impedance."""
    tree_path = _impedance_tree_path(port)
    script = VBAScript()
    script.add_comment(f"Extract input impedance (Z{port},{port}) vs frequency")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "1D Results\\Z-Parameters\\Z{port},{port}"',
        "",
        "  ' Read impedance data (real part)",
        "  Dim nPoints As Long",
        '  nPoints = Result1D("").GetN',
        "",
        "  Dim i As Long",
        "  For i = 0 To nPoints - 1",
        "    Dim freq As Double, zReal As Double, zImag As Double",
        '    freq = Result1D("").GetX(i)',
        '    zReal = Result1D("").GetY(i)',
        "",
        "    ' Switch to imaginary part",
        f'    SelectTreeItem "1D Results\\Z-Parameters\\Z{port},{port}_imag"',
        '    zImag = Result1D("").GetY(i)',
        "",
        "    ' Switch back to real part for next iteration",
        f'    SelectTreeItem "1D Results\\Z-Parameters\\Z{port},{port}"',
        '    Debug.Print freq & "," & zReal & "," & zImag',
        "  Next i",
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_vswr_vba(port: int) -> str:
    """Build VBA script for extracting VSWR."""
    tree_path = _vswr_tree_path(port)
    script = VBAScript()
    script.add_comment(f"Extract VSWR for port {port}")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_comment("VSWR = (1 + |S11|) / (1 - |S11|)")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{tree_path}"',
        "",
        "  ' If VSWR is directly available in the result tree, read it",
        "  Dim nPoints As Long",
        '  nPoints = Result1D("").GetN',
        "",
        "  If nPoints > 0 Then",
        "    Dim i As Long",
        "    For i = 0 To nPoints - 1",
        "      Dim freq As Double, vswr As Double",
        '      freq = Result1D("").GetX(i)',
        '      vswr = Result1D("").GetY(i)',
        '      Debug.Print freq & "," & vswr',
        "    Next i",
        "  Else",
        "    ' Compute VSWR from S-parameters",
        f'    SelectTreeItem "1D Results\\S-Parameters\\S{port},{port}"',
        '    nPoints = Result1D("").GetN',
        "    For i = 0 To nPoints - 1",
        '      freq = Result1D("").GetX(i)',
        "      Dim s11_db As Double, s11_mag As Double",
        '      s11_db = Result1D("").GetY(i)',
        "      s11_mag = 10^(s11_db / 20.0)",
        "      vswr = (1 + s11_mag) / (1 - s11_mag)",
        "      If vswr < 0 Then vswr = 999  ' Clamp if |S11| > 1",
        '      Debug.Print freq & "," & vswr',
        "    Next i",
        "  End If",
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_gain_vba(frequency: float) -> str:
    """Build VBA script for extracting antenna gain."""
    tree_path = _farfield_tree_path(frequency)
    script = VBAScript()
    script.add_comment(f"Extract antenna gain at {frequency} GHz")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{tree_path}"',
        "",
        "  Dim ff As Object",
        "  Set ff = FarfieldPlot",
        "  ff.Reset",
        f'  ff.Plottype "3D"',
        f'  ff.SetPlotMode "Gain"',
        "",
        "  ' Get peak gain",
        '  Dim peakGain As Double',
        '  peakGain = ff.GetResultValue("max gain")',
        '  Debug.Print "Peak Gain (dBi): " & peakGain',
        "",
        "  ' Get direction of maximum gain",
        '  Dim theta As Double, phi As Double',
        '  theta = ff.GetResultValue("main lobe direction, theta")',
        '  phi = ff.GetResultValue("main lobe direction, phi")',
        '  Debug.Print "Max Gain Direction: theta=" & theta & ", phi=" & phi',
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_efficiency_vba(frequency: float) -> str:
    """Build VBA script for extracting radiation efficiency."""
    tree_path = _farfield_tree_path(frequency)
    script = VBAScript()
    script.add_comment(f"Extract antenna efficiency at {frequency} GHz")
    script.add_comment(f"Result tree path: {tree_path}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{tree_path}"',
        "",
        "  Dim ff As Object",
        "  Set ff = FarfieldPlot",
        "  ff.Reset",
        "",
        "  ' Radiation efficiency (excludes mismatch loss)",
        '  Dim radEff As Double',
        '  radEff = ff.GetResultValue("rad. efficiency")',
        '  Debug.Print "Radiation Efficiency: " & radEff',
        "",
        "  ' Total efficiency (includes mismatch loss)",
        '  Dim totEff As Double',
        '  totEff = ff.GetResultValue("tot. efficiency")',
        '  Debug.Print "Total Efficiency: " & totEff',
        "",
        "  ' Mismatch loss in dB",
        "  Dim mismatch As Double",
        "  If totEff > 0 And radEff > 0 Then",
        "    mismatch = 10 * Log(totEff / radEff) / Log(10)",
        "  Else",
        "    mismatch = -99",
        "  End If",
        '  Debug.Print "Mismatch Loss (dB): " & mismatch',
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_list_results_vba(tree_path: str | None) -> str:
    """Build VBA script for listing result tree items."""
    root = tree_path or "1D Results"
    script = VBAScript()
    script.add_comment(f"List available results under: {root}")
    script.add_blank()

    lines = [
        "Sub Main()",
        f'  SelectTreeItem "{root}"',
        "",
        "  ' Enumerate child items in the result tree",
        "  Dim sItem As String",
        f'  sItem = ResultTree.GetFirstChildName("{root}")',
        "",
        '  Do While sItem <> ""',
        '    Debug.Print sItem',
        f'    sItem = ResultTree.GetNextItemName("{root}")',
        "  Loop",
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


def _build_export_result_vba(
    result_path: str, output_file: str, fmt: str
) -> str:
    """Build VBA script for exporting a result to file."""
    script = VBAScript()
    script.add_comment(f"Export result '{result_path}' to {fmt.upper()}: {output_file}")
    script.add_blank()

    if fmt == "touchstone":
        lines = [
            "Sub Main()",
            f'  SelectTreeItem "{result_path}"',
            "",
            "  ' Export S-parameters in Touchstone format",
            "  Dim sTouchstone As Object",
            "  Set sTouchstone = TouchstoneExport",
            "  sTouchstone.Reset",
            f'  sTouchstone.FileName "{output_file}"',
            '  sTouchstone.FrequencyRange "Full"',
            "  sTouchstone.Renormalize 50",
            '  sTouchstone.UseARResults "False"',
            "  sTouchstone.Write",
            "End Sub",
        ]
    elif fmt == "csv":
        lines = [
            "Sub Main()",
            f'  SelectTreeItem "{result_path}"',
            "",
            "  ' Export result data as ASCII/CSV",
            "  Dim res As Object",
            '  Set res = Result1D("")',
            "",
            "  Dim nPoints As Long",
            "  nPoints = res.GetN",
            "",
            f'  Open "{output_file}" For Output As #1',
            '  Print #1, "Frequency,Value"',
            "",
            "  Dim i As Long",
            "  For i = 0 To nPoints - 1",
            "    Print #1, res.GetX(i) & \",\" & res.GetY(i)",
            "  Next i",
            "  Close #1",
            "End Sub",
        ]
    else:
        # txt format
        lines = [
            "Sub Main()",
            f'  SelectTreeItem "{result_path}"',
            "",
            "  ' Export result data as space-separated text",
            "  Dim res As Object",
            '  Set res = Result1D("")',
            "",
            "  Dim nPoints As Long",
            "  nPoints = res.GetN",
            "",
            f'  Open "{output_file}" For Output As #1',
            "",
            "  Dim i As Long",
            "  For i = 0 To nPoints - 1",
            '    Print #1, res.GetX(i) & " " & res.GetY(i)',
            "  Next i",
            "  Close #1",
            "End Sub",
        ]

    script.add_raw("\n".join(lines))
    return script.build()


def _build_result_summary_vba() -> str:
    """Build VBA script for getting a summary of all key results."""
    script = VBAScript()
    script.add_comment("Get summary of all key simulation results")
    script.add_blank()

    lines = [
        "Sub Main()",
        "  ' --- S-Parameters ---",
        '  Debug.Print "=== S-Parameters ==="',
        '  SelectTreeItem "1D Results\\S-Parameters\\S1,1"',
        "  Dim nPoints As Long",
        '  nPoints = Result1D("").GetN',
        "",
        "  If nPoints > 0 Then",
        "    ' Find minimum S11 (best match)",
        "    Dim minS11 As Double, minFreq As Double",
        "    minS11 = 0",
        "    Dim i As Long",
        "    For i = 0 To nPoints - 1",
        "      Dim s11 As Double",
        '      s11 = Result1D("").GetY(i)',
        "      If s11 < minS11 Then",
        "        minS11 = s11",
        '        minFreq = Result1D("").GetX(i)',
        "      End If",
        "    Next i",
        '    Debug.Print "Best S11: " & minS11 & " dB at " & minFreq & " GHz"',
        "  End If",
        "",
        "  ' --- Impedance ---",
        '  Debug.Print "=== Impedance ==="',
        '  SelectTreeItem "1D Results\\Z-Parameters\\Z1,1"',
        '  nPoints = Result1D("").GetN',
        "  If nPoints > 0 Then",
        "    ' Report impedance at center frequency",
        "    Dim midIdx As Long",
        "    midIdx = nPoints \\ 2",
        '    Debug.Print "Z at mid-band: " & Result1D("").GetY(midIdx) & " Ohm"',
        "  End If",
        "",
        "  ' --- Far-field (if available) ---",
        '  Debug.Print "=== Far-field ==="',
        "  Dim ff As Object",
        "  Set ff = FarfieldPlot",
        "  ff.Reset",
        '  ff.SetPlotMode "Gain"',
        '  Debug.Print "Peak Gain: " & ff.GetResultValue("max gain") & " dBi"',
        '  Debug.Print "Directivity: " & ff.GetResultValue("directivity") & " dBi"',
        '  Debug.Print "Rad. Efficiency: " & ff.GetResultValue("rad. efficiency")',
        '  Debug.Print "Tot. Efficiency: " & ff.GetResultValue("tot. efficiency")',
        "End Sub",
    ]
    script.add_raw("\n".join(lines))
    return script.build()


# ---------------------------------------------------------------------------
# Default result tree structure (offline mode)
# ---------------------------------------------------------------------------

_DEFAULT_RESULT_TREE: dict[str, list[str]] = {
    "": [
        "1D Results",
        "2D/3D Results",
        "Farfields",
        "Tables",
    ],
    "1D Results": [
        "S-Parameters",
        "Z-Parameters",
        "Y-Parameters",
        "VSWR",
        "Balance",
        "Power",
        "Energy",
    ],
    "1D Results\\S-Parameters": [
        "S1,1",
        "S2,1",
        "S1,2",
        "S2,2",
    ],
    "1D Results\\Z-Parameters": [
        "Z1,1",
        "Z2,2",
    ],
    "1D Results\\VSWR": [
        "VSWR1",
        "VSWR2",
    ],
    "Farfields": [
        "farfield (f=<frequency>)",
    ],
    "2D/3D Results": [
        "E-Field",
        "H-Field",
        "Surface Current",
        "Power Flow",
        "Power Loss Density",
    ],
    "Tables": [
        "1D Results",
        "0D Results",
    ],
}


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------

async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a result extraction tool call.

    Returns a list of TextContent with JSON-encoded results.
    """

    # ------------------------------------------------------------------
    # cst_get_s_parameters
    # ------------------------------------------------------------------
    if name == "cst_get_s_parameters":
        port_in = arguments.get("port_in", 1)
        port_out = arguments.get("port_out", 1)
        fmt = arguments.get("format", "db")

        validate_port_number(port_in)
        validate_port_number(port_out)

        valid_formats = ["db", "mag", "real_imag", "phase"]
        if fmt not in valid_formats:
            return _text({
                "status": "error",
                "message": f"Invalid format '{fmt}'. Must be one of: {valid_formats}",
            })

        tree_path = _s_param_tree_path(port_out, port_in)

        if client.connected:
            result = client.get_result(tree_path)
            result["s_parameter"] = f"S{port_out},{port_in}"
            result["format"] = fmt
            result["tree_path"] = tree_path
            return _text(result)

        # Offline mode
        vba = _build_s_parameter_vba(port_out, port_in, fmt)
        return _text({
            "status": "offline",
            "s_parameter": f"S{port_out},{port_in}",
            "format": fmt,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "CST stores S-parameters under '1D Results\\S-Parameters'. "
                    "Each S-parameter is named SX,Y where X is the output port "
                    "and Y is the input port. Phase data is in SX,Y_phase, "
                    "real/imaginary in SX,Y_real and SX,Y_imag."
                ),
                "common_paths": [
                    "1D Results\\S-Parameters\\S1,1  (reflection at port 1)",
                    "1D Results\\S-Parameters\\S2,1  (transmission port 1 to 2)",
                    "1D Results\\S-Parameters\\S1,2  (transmission port 2 to 1)",
                    "1D Results\\S-Parameters\\S2,2  (reflection at port 2)",
                ],
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite (Macros > Run Macro) "
                "after the simulation has completed. Results are printed to "
                "the CST message window."
            ),
        })

    # ------------------------------------------------------------------
    # cst_get_farfield
    # ------------------------------------------------------------------
    if name == "cst_get_farfield":
        frequency = arguments["frequency"]
        monitor_name = arguments.get("monitor_name")

        validate_frequency(frequency)

        tree_path = _farfield_tree_path(frequency, monitor_name)

        if client.connected:
            result = client.get_result(tree_path)
            result["frequency_ghz"] = frequency
            result["tree_path"] = tree_path
            return _text(result)

        vba = _build_farfield_vba(frequency, monitor_name)
        return _text({
            "status": "offline",
            "frequency_ghz": frequency,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "CST stores far-field results under 'Farfields'. Each "
                    "far-field monitor creates a result named "
                    "'farfield (f=<freq>)'. The far-field contains gain, "
                    "directivity, efficiency, beam widths, and 3D radiation "
                    "pattern data."
                ),
                "available_metrics": [
                    "max gain (dBi)",
                    "directivity (dBi)",
                    "radiation efficiency",
                    "total efficiency",
                    "angular width (3dB) theta",
                    "angular width (3dB) phi",
                    "main lobe direction theta",
                    "main lobe direction phi",
                    "front-to-back ratio (dB)",
                    "side lobe level (dB)",
                ],
            },
            "prerequisite": (
                "A farfield monitor must be defined at the desired frequency "
                "BEFORE running the simulation. Use cst_add_field_monitor with "
                "monitor_type='Farfield'."
            ),
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed. Ensure a farfield monitor exists at the "
                "specified frequency."
            ),
        })

    # ------------------------------------------------------------------
    # cst_add_field_monitor
    # ------------------------------------------------------------------
    if name == "cst_add_field_monitor":
        monitor_type = arguments["monitor_type"]
        frequency = arguments["frequency"]
        monitor_name = arguments.get("name")

        validate_frequency(frequency)

        valid_types = [e.value for e in FieldMonitorType]
        if monitor_type not in valid_types:
            return _text({
                "status": "error",
                "message": f"Invalid monitor_type '{monitor_type}'. Must be one of: {valid_types}",
            })

        vba = _build_add_monitor_vba(monitor_type, frequency, monitor_name)
        result = client.execute_vba(vba)
        result["monitor_type"] = monitor_type
        result["frequency_ghz"] = frequency

        if monitor_name:
            result["monitor_name"] = monitor_name
        else:
            type_prefix_map = {
                "Efield": "e-field",
                "Hfield": "h-field",
                "Powerflow": "power",
                "Current": "current",
                "Powerloss": "loss",
                "Farfield": "farfield",
                "Surfacecurrent": "surface-current",
                "Eenergy": "e-energy",
                "Henergy": "h-energy",
            }
            prefix = type_prefix_map.get(monitor_type, monitor_type.lower())
            result["monitor_name"] = f"{prefix} (f={frequency})"

        if not client.connected:
            result["instructions"] = (
                "Run the VBA script in CST Studio Suite to add the field "
                "monitor. Monitors must be added BEFORE starting the "
                "simulation. After adding monitors, re-run the solver to "
                "generate field results at the monitored frequencies."
            )

        return _text(result)

    # ------------------------------------------------------------------
    # cst_get_impedance
    # ------------------------------------------------------------------
    if name == "cst_get_impedance":
        port = arguments.get("port", 1)
        validate_port_number(port)

        tree_path = _impedance_tree_path(port)

        if client.connected:
            result = client.get_result(tree_path)
            result["port"] = port
            result["tree_path"] = tree_path
            return _text(result)

        vba = _build_impedance_vba(port)
        return _text({
            "status": "offline",
            "port": port,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "CST stores impedance data under '1D Results\\Z-Parameters'. "
                    "ZX,X gives the input impedance at port X. The real part "
                    "is in ZX,X and imaginary in ZX,X_imag. At resonance, the "
                    "imaginary part crosses zero and the real part should be "
                    "close to 50 ohms for a matched antenna."
                ),
                "common_paths": [
                    "1D Results\\Z-Parameters\\Z1,1  (input impedance port 1)",
                    "1D Results\\Z-Parameters\\Z1,1_imag  (imaginary part)",
                    "1D Results\\Z-Parameters\\Z2,2  (input impedance port 2)",
                ],
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed. Output is printed as freq,Re(Z),Im(Z) to the "
                "CST message window."
            ),
        })

    # ------------------------------------------------------------------
    # cst_get_vswr
    # ------------------------------------------------------------------
    if name == "cst_get_vswr":
        port = arguments.get("port", 1)
        validate_port_number(port)

        tree_path = _vswr_tree_path(port)

        if client.connected:
            result = client.get_result(tree_path)
            result["port"] = port
            result["tree_path"] = tree_path
            return _text(result)

        vba = _build_vswr_vba(port)
        return _text({
            "status": "offline",
            "port": port,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "VSWR (Voltage Standing Wave Ratio) indicates impedance "
                    "matching quality. CST may store VSWR directly under "
                    "'1D Results\\VSWR\\VSWRX', or it can be computed from "
                    "S-parameters. VSWR = (1+|S11|)/(1-|S11|). "
                    "A VSWR < 2.0 corresponds to S11 < -9.5 dB (acceptable). "
                    "VSWR = 1.0 is a perfect match."
                ),
                "reference": {
                    "VSWR 1.0": "Perfect match (S11 = -inf dB)",
                    "VSWR 1.5": "S11 = -14 dB (good)",
                    "VSWR 2.0": "S11 = -9.5 dB (acceptable)",
                    "VSWR 3.0": "S11 = -6 dB (marginal)",
                },
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed. If VSWR is not directly available, the script "
                "computes it from S-parameters."
            ),
        })

    # ------------------------------------------------------------------
    # cst_get_gain
    # ------------------------------------------------------------------
    if name == "cst_get_gain":
        frequency = arguments["frequency"]
        validate_frequency(frequency)

        tree_path = _farfield_tree_path(frequency)

        if client.connected:
            result = client.get_result(tree_path)
            result["frequency_ghz"] = frequency
            result["tree_path"] = tree_path
            return _text(result)

        vba = _build_gain_vba(frequency)
        return _text({
            "status": "offline",
            "frequency_ghz": frequency,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "Antenna gain is extracted from the far-field result at "
                    "the specified frequency. CST reports realized gain "
                    "(includes mismatch loss) and IEEE gain (excludes mismatch "
                    "loss). The gain is given in dBi (relative to isotropic). "
                    "The direction of maximum gain is reported as (theta, phi) "
                    "in the CST spherical coordinate system."
                ),
                "gain_types": {
                    "IEEE Gain": "Excludes mismatch loss (feed efficiency)",
                    "Realized Gain": "Includes mismatch loss",
                    "Directivity": "Excludes all losses",
                },
            },
            "prerequisite": (
                "A farfield monitor must be defined at the desired frequency "
                "BEFORE running the simulation."
            ),
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed."
            ),
        })

    # ------------------------------------------------------------------
    # cst_get_efficiency
    # ------------------------------------------------------------------
    if name == "cst_get_efficiency":
        frequency = arguments["frequency"]
        validate_frequency(frequency)

        tree_path = _farfield_tree_path(frequency)

        if client.connected:
            result = client.get_result(tree_path)
            result["frequency_ghz"] = frequency
            result["tree_path"] = tree_path
            return _text(result)

        vba = _build_efficiency_vba(frequency)
        return _text({
            "status": "offline",
            "frequency_ghz": frequency,
            "tree_path": tree_path,
            "result_tree_info": {
                "description": (
                    "CST reports multiple efficiency metrics from the "
                    "far-field result: radiation efficiency (power radiated / "
                    "power accepted, excludes mismatch), total efficiency "
                    "(power radiated / power stimulated, includes mismatch), "
                    "and mismatch loss. Total efficiency = radiation efficiency "
                    "x (1 - |S11|^2)."
                ),
                "efficiency_definitions": {
                    "radiation_efficiency": (
                        "Ratio of radiated power to accepted power. "
                        "Accounts for conductor and dielectric losses only."
                    ),
                    "total_efficiency": (
                        "Ratio of radiated power to stimulated (incident) "
                        "power. Includes mismatch loss at the feed."
                    ),
                    "mismatch_loss_db": (
                        "Loss due to impedance mismatch at the feed point. "
                        "mismatch_loss = 10*log10(total_eff / rad_eff)."
                    ),
                },
            },
            "prerequisite": (
                "A farfield monitor must be defined at the desired frequency "
                "BEFORE running the simulation."
            ),
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed."
            ),
        })

    # ------------------------------------------------------------------
    # cst_list_results
    # ------------------------------------------------------------------
    if name == "cst_list_results":
        tree_path = arguments.get("tree_path")

        if client.connected:
            vba = _build_list_results_vba(tree_path)
            result = client.execute_vba(vba)
            result["tree_path"] = tree_path or "(all result categories)"
            return _text(result)

        # Offline: return the default result tree structure
        lookup_key = tree_path or ""
        items = _DEFAULT_RESULT_TREE.get(lookup_key, [])
        vba = _build_list_results_vba(tree_path)

        return _text({
            "status": "offline",
            "tree_path": tree_path or "(all result categories)",
            "items": items,
            "result_tree_structure": {
                "description": (
                    "The CST result tree organizes simulation outputs "
                    "hierarchically. The structure below shows the typical "
                    "layout after a simulation completes."
                ),
                "typical_structure": {
                    "1D Results": {
                        "S-Parameters": ["S1,1", "S2,1", "S1,2", "S2,2"],
                        "Z-Parameters": ["Z1,1", "Z2,2"],
                        "Y-Parameters": ["Y1,1", "Y2,2"],
                        "VSWR": ["VSWR1", "VSWR2"],
                        "Power": ["Stimulated", "Accepted", "Radiated"],
                        "Energy": ["Total Energy vs Time"],
                    },
                    "Farfields": ["farfield (f=<freq>)"],
                    "2D/3D Results": [
                        "E-Field", "H-Field", "Surface Current",
                        "Power Flow", "Power Loss Density",
                    ],
                    "Tables": ["1D Results", "0D Results"],
                },
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite to get the actual "
                "result tree contents. The tree structure depends on the "
                "solver type used and monitors defined."
            ),
        })

    # ------------------------------------------------------------------
    # cst_export_result
    # ------------------------------------------------------------------
    if name == "cst_export_result":
        result_path = arguments["result_path"]
        output_file = arguments["output_file"]
        fmt = arguments.get("format", "csv")

        validate_file_path(output_file)

        valid_formats = ["csv", "touchstone", "txt"]
        if fmt not in valid_formats:
            return _text({
                "status": "error",
                "message": f"Invalid format '{fmt}'. Must be one of: {valid_formats}",
            })

        vba = _build_export_result_vba(result_path, output_file, fmt)

        if client.connected:
            result = client.execute_vba(vba)
            if result.get("status") != "error":
                result["result_path"] = result_path
                result["output_file"] = output_file
                result["format"] = fmt
            return _text(result)

        return _text({
            "status": "offline",
            "result_path": result_path,
            "output_file": output_file,
            "format": fmt,
            "format_notes": {
                "csv": (
                    "Comma-separated values with header row. "
                    "Compatible with Excel, MATLAB, Python pandas."
                ),
                "touchstone": (
                    "Industry-standard Touchstone/SnP format for S-parameters. "
                    "Compatible with all RF/microwave EDA tools. "
                    "Use .s1p for 1-port, .s2p for 2-port, etc."
                ),
                "txt": (
                    "Space-separated text file. Lightweight format for "
                    "quick data exchange."
                ),
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed. The result will be exported to the specified "
                "file path on the Windows machine running CST."
            ),
        })

    # ------------------------------------------------------------------
    # cst_get_result_summary
    # ------------------------------------------------------------------
    if name == "cst_get_result_summary":
        if client.connected:
            vba = _build_result_summary_vba()
            result = client.execute_vba(vba)
            result["summary_type"] = "full"
            return _text(result)

        vba = _build_result_summary_vba()
        return _text({
            "status": "offline",
            "summary_type": "full",
            "description": (
                "The result summary script queries all major simulation "
                "outputs in one pass: S-parameters (best match frequency "
                "and depth), input impedance at mid-band, and far-field "
                "metrics (gain, directivity, efficiency). This provides a "
                "quick design evaluation without extracting each result "
                "individually."
            ),
            "metrics_included": {
                "S-Parameters": {
                    "best_s11_db": "Minimum S11 value in dB (deepest match)",
                    "best_match_freq_ghz": "Frequency of best impedance match",
                },
                "Impedance": {
                    "z_midband_ohm": "Input impedance at mid-band frequency",
                },
                "Farfield": {
                    "peak_gain_dbi": "Maximum antenna gain",
                    "directivity_dbi": "Peak directivity",
                    "radiation_efficiency": "Radiation efficiency (0 to 1)",
                    "total_efficiency": "Total efficiency including mismatch",
                },
            },
            "vba_script": vba,
            "instructions": (
                "Run the VBA script in CST Studio Suite after the simulation "
                "has completed. All key metrics are printed to the CST message "
                "window in a structured format."
            ),
        })

    # ------------------------------------------------------------------
    # Unknown tool
    # ------------------------------------------------------------------
    return _text({
        "status": "error",
        "message": f"Unknown result tool: {name}",
    })


# ---------------------------------------------------------------------------
# Registration helper (used by tools/__init__.py)
# ---------------------------------------------------------------------------

_TOOL_NAMES: set[str] = {tool.name for tool in TOOLS}


def register_result_tools(server: Server, client: CSTClient) -> None:
    """Register result extraction tools with the MCP server."""
    from mcp_cst_studio.tools import _registry
    _registry.add_module(TOOLS, handle, client)
