"""Antenna optimization tools for CST Studio Suite.

Provides 2 MCP tools for evaluating antenna performance against band/VSWR
goals and running automated Nelder-Mead refinement loops.

- ``cst_evaluate_antenna``: Read-only evaluation of S11 against VSWR targets
- ``cst_refine_antenna``: Iterative parameter optimization via Nelder-Mead
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from typing import TYPE_CHECKING

from mcp.types import TextContent, Tool

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.vba_builder import VBABuilder, VBAScript

if TYPE_CHECKING:
    from mcp.server import Server

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="cst_evaluate_antenna",
        description=(
            "Evaluate current antenna simulation results against performance goals. "
            "Exports S-parameter data and checks VSWR (or return loss) against "
            "per-band targets. Read-only — does not modify the model. "
            "Returns pass/fail per band, worst VSWR, and detected resonances."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "bands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Band label (e.g. '2.4 GHz WiFi').",
                            },
                            "f_low_ghz": {
                                "type": "number",
                                "description": "Lower band edge in GHz.",
                            },
                            "f_high_ghz": {
                                "type": "number",
                                "description": "Upper band edge in GHz.",
                            },
                            "vswr_target": {
                                "type": "number",
                                "description": "Maximum acceptable VSWR (e.g. 2.0 or 2.5).",
                                "default": 2.5,
                            },
                        },
                        "required": ["name", "f_low_ghz", "f_high_ghz"],
                    },
                    "minItems": 1,
                    "description": "Frequency bands to evaluate.",
                },
                "port": {
                    "type": "integer",
                    "description": "Port number for S-parameter (default: 1).",
                    "default": 1,
                },
            },
            "required": ["bands"],
        },
    ),
    Tool(
        name="cst_refine_antenna",
        description=(
            "Run an automated Nelder-Mead optimization loop to tune CST design "
            "parameters toward VSWR goals across specified frequency bands. "
            "Each iteration sets parameters, runs the solver, exports S11, and "
            "evaluates against targets. Uses silent VBA execution to avoid "
            "history bloat. Applies the best parameters permanently at the end. "
            "Connected mode only — requires a live CST session with a solvable project."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "CST parameter name.",
                            },
                            "initial": {
                                "type": "number",
                                "description": "Initial value.",
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
                        "required": ["name", "initial", "min", "max"],
                    },
                    "minItems": 1,
                    "description": "Parameters to optimize with initial values and bounds.",
                },
                "bands": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "f_low_ghz": {"type": "number"},
                            "f_high_ghz": {"type": "number"},
                            "vswr_target": {
                                "type": "number",
                                "default": 2.5,
                            },
                        },
                        "required": ["name", "f_low_ghz", "f_high_ghz"],
                    },
                    "minItems": 1,
                    "description": "Frequency bands with VSWR targets.",
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum optimization iterations (default: 20).",
                    "default": 20,
                },
                "port": {
                    "type": "integer",
                    "description": "Port number (default: 1).",
                    "default": 1,
                },
            },
            "required": ["parameters", "bands"],
        },
    ),
]

_TOOL_NAMES = {t.name for t in TOOLS}

# ---------------------------------------------------------------------------
# S-parameter parsing and VSWR math
# ---------------------------------------------------------------------------


def _parse_s11_data(filepath: str) -> tuple[list[float], list[float]]:
    """Parse CST ASCIIExport space-separated S11 data.

    Expected format (2-line header, then space-separated freq + S11_dB):
        Frequency / GHz    S1,1/abs,dB
        -----------------------------------------
        1.0                -0.053
        1.035              -0.061
        ...

    Returns (frequencies_ghz, s11_db).
    """
    freqs: list[float] = []
    s11: list[float] = []

    with open(filepath, "r") as f:
        lines = f.readlines()

    # Skip header lines (non-numeric or separator lines)
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("-") or stripped.startswith("F"):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            try:
                freqs.append(float(parts[0]))
                s11.append(float(parts[1]))
            except ValueError:
                continue

    if not freqs:
        raise ValueError(f"No data found in {filepath}")

    return freqs, s11


def s11_to_vswr(s11_db: float) -> float:
    """Convert S11 in dB to VSWR.

    VSWR = (1 + |Γ|) / (1 - |Γ|)  where |Γ| = 10^(S11_dB/20)
    """
    if s11_db >= 0:
        return float("inf")
    gamma = 10 ** (s11_db / 20.0)
    if gamma >= 1.0:
        return float("inf")
    return (1.0 + gamma) / (1.0 - gamma)


def vswr_to_s11(vswr: float) -> float:
    """Convert VSWR to S11 in dB.

    |Γ| = (VSWR - 1) / (VSWR + 1)
    S11_dB = 20 * log10(|Γ|)
    """
    if vswr <= 1.0:
        return -float("inf")
    gamma = (vswr - 1.0) / (vswr + 1.0)
    if gamma <= 0:
        return -float("inf")
    return 20.0 * math.log10(gamma)


def _evaluate_bands(
    freqs: list[float],
    s11_db: list[float],
    bands: list[dict],
) -> list[dict]:
    """Evaluate VSWR performance per band.

    Returns a list of dicts with band name, worst VSWR, target, pass/fail.
    """
    results = []
    for band in bands:
        f_low = band["f_low_ghz"]
        f_high = band["f_high_ghz"]
        target = band.get("vswr_target", 2.5)

        # Filter points within band
        band_vswr = []
        band_s11 = []
        for f, s in zip(freqs, s11_db):
            if f_low <= f <= f_high:
                band_vswr.append(s11_to_vswr(s))
                band_s11.append(s)

        if not band_vswr:
            results.append({
                "name": band["name"],
                "status": "NO_DATA",
                "message": f"No data points in {f_low}-{f_high} GHz",
                "target_vswr": target,
            })
            continue

        worst_vswr = max(band_vswr)
        worst_s11 = max(band_s11)  # Least negative = worst match
        best_vswr = min(band_vswr)
        best_idx = band_vswr.index(best_vswr)
        best_s11 = band_s11[best_idx]

        # Find frequency of best match within band
        band_freqs = [f for f in freqs if f_low <= f <= f_high]
        best_freq = band_freqs[best_idx] if best_idx < len(band_freqs) else None

        passed = worst_vswr <= target
        target_s11 = vswr_to_s11(target)
        margin_db = target_s11 - worst_s11  # Negative = pass, positive = fail

        results.append({
            "name": band["name"],
            "status": "PASS" if passed else "FAIL",
            "worst_vswr": round(worst_vswr, 3),
            "worst_s11_db": round(worst_s11, 2),
            "best_vswr": round(best_vswr, 3),
            "best_s11_db": round(best_s11, 2),
            "best_freq_ghz": round(best_freq, 4) if best_freq else None,
            "target_vswr": target,
            "target_s11_db": round(target_s11, 2),
            "margin_db": round(margin_db, 2),
            "num_points": len(band_vswr),
        })

    return results


def _find_resonances(
    freqs: list[float],
    s11_db: list[float],
    threshold_db: float = -5.0,
) -> list[dict]:
    """Find resonant frequencies (local minima of S11 below threshold).

    Returns list of {freq_ghz, s11_db, vswr} for each detected resonance.
    """
    resonances = []
    n = len(s11_db)
    if n < 3:
        return resonances

    for i in range(1, n - 1):
        if s11_db[i] < threshold_db:
            if s11_db[i] <= s11_db[i - 1] and s11_db[i] <= s11_db[i + 1]:
                resonances.append({
                    "freq_ghz": round(freqs[i], 4),
                    "s11_db": round(s11_db[i], 2),
                    "vswr": round(s11_to_vswr(s11_db[i]), 3),
                })

    return resonances


def _compute_cost(
    freqs: list[float],
    s11_db: list[float],
    bands: list[dict],
) -> tuple[float, list[dict]]:
    """Compute optimization cost: sum of VSWR violations across bands.

    Cost = sum(max(0, worst_band_vswr - target) for each band)
    Returns (cost, band_results).
    """
    band_results = _evaluate_bands(freqs, s11_db, bands)
    cost = 0.0
    for br in band_results:
        if br["status"] == "NO_DATA":
            cost += 10.0  # Penalty for missing data
        elif br["status"] == "FAIL":
            cost += br["worst_vswr"] - br["target_vswr"]
    return cost, band_results


# ---------------------------------------------------------------------------
# VBA generation helpers
# ---------------------------------------------------------------------------


def _set_params_and_solve_vba(params: dict[str, float], export_path: str | None = None, port: int = 1) -> str:
    """Build raw VBA that sets parameters, solves, and optionally exports.

    Combines everything in a single ``add_to_history`` call.  This is
    necessary because:
    - ``RebuildOnParametricChange`` is rejected inside a structure macro
    - ``StoreParameter`` alone doesn't trigger a rebuild
    - ``Solver.Start`` triggers the rebuild automatically before solving

    The "Results May Get Incompatible" dialog is handled by the
    background DialogWatcher.
    """
    lines = []
    for name, value in params.items():
        lines.append(f'StoreParameter "{name}", "{value}"')
    lines.append("Solver.Start")
    if export_path:
        safe_path = export_path.replace("\\", "/")
        tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
        lines.append("")
        lines.append(f'SelectTreeItem "{tree_path}"')
        lines.append("With ASCIIExport")
        lines.append("  .Reset")
        lines.append(f'  .FileName "{safe_path}"')
        lines.append('  .SetfileType "csv"')
        lines.append("  .Execute")
        lines.append("End With")
    return "\n".join(lines)


def _set_params_vba(params: dict[str, float]) -> str:
    """Build raw VBA for just StoreParameter calls (for final application)."""
    lines = []
    for name, value in params.items():
        lines.append(f'StoreParameter "{name}", "{value}"')
    return "\n".join(lines)


def _run_solver_vba() -> str:
    """Build raw VBA to start the time-domain solver.

    Note: Solver.Start cannot run inside schematic.execute_vba_code()
    (structure macro context). Must use model3d.add_to_history() instead.
    """
    return "Solver.Start"


def _export_s11_vba(filepath: str, port: int = 1) -> str:
    """Build raw VBA to export S11 via ASCIIExport.

    Note: ASCIIExport doesn't work in schematic.execute_vba_code() context.
    Must use model3d.add_to_history() instead.
    """
    safe_path = filepath.replace("\\", "/")
    tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
    return (
        f'SelectTreeItem "{tree_path}"\n'
        "With ASCIIExport\n"
        "  .Reset\n"
        f'  .FileName "{safe_path}"\n'
        '  .SetfileType "csv"\n'
        "  .Execute\n"
        "End With"
    )


def _solve_and_export_vba(filepath: str, port: int = 1) -> str:
    """Build raw VBA that runs solver then exports S11.

    Combines both operations in a single add_to_history call to minimize
    history bloat during optimization.
    """
    safe_path = filepath.replace("\\", "/")
    tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
    return (
        "Solver.Start\n"
        "\n"
        f'SelectTreeItem "{tree_path}"\n'
        "With ASCIIExport\n"
        "  .Reset\n"
        f'  .FileName "{safe_path}"\n'
        '  .SetfileType "csv"\n'
        "  .Execute\n"
        "End With"
    )


# ---------------------------------------------------------------------------
# Nelder-Mead simplex optimizer (no scipy dependency)
# ---------------------------------------------------------------------------


def _build_initial_simplex(
    x0: list[float],
    bounds: list[tuple[float, float]],
) -> list[list[float]]:
    """Build initial Nelder-Mead simplex (N+1 vertices for N parameters).

    Each vertex perturbs one parameter by ~10% of its range from x0,
    clamped to bounds.
    """
    n = len(x0)
    simplex = [list(x0)]  # First vertex is the initial point

    for i in range(n):
        vertex = list(x0)
        span = bounds[i][1] - bounds[i][0]
        delta = 0.25 * span  # 25% of range for meaningful exploration
        # Perturb away from nearest bound to maximise exploration
        mid = (bounds[i][0] + bounds[i][1]) / 2.0
        if x0[i] >= mid:
            vertex[i] = x0[i] - delta
        else:
            vertex[i] = x0[i] + delta
        # Clamp to bounds
        vertex[i] = max(bounds[i][0], min(bounds[i][1], vertex[i]))
        simplex.append(vertex)

    return simplex


def _clamp_to_bounds(
    point: list[float],
    bounds: list[tuple[float, float]],
) -> list[float]:
    """Clamp each coordinate to its bounds."""
    return [max(lo, min(hi, v)) for v, (lo, hi) in zip(point, bounds)]


def _centroid(simplex: list[list[float]], exclude_idx: int) -> list[float]:
    """Compute centroid of all simplex vertices except exclude_idx."""
    n = len(simplex[0])
    m = len(simplex) - 1  # Number of points to average
    c = [0.0] * n
    for i, vertex in enumerate(simplex):
        if i == exclude_idx:
            continue
        for j in range(n):
            c[j] += vertex[j]
    return [v / m for v in c]


def _reflect(centroid: list[float], worst: list[float], alpha: float = 1.0) -> list[float]:
    """Reflect worst point through centroid."""
    return [centroid[j] + alpha * (centroid[j] - worst[j]) for j in range(len(centroid))]


def _expand(centroid: list[float], reflected: list[float], gamma: float = 2.0) -> list[float]:
    """Expand reflected point further from centroid."""
    return [centroid[j] + gamma * (reflected[j] - centroid[j]) for j in range(len(centroid))]


def _contract(centroid: list[float], point: list[float], rho: float = 0.5) -> list[float]:
    """Contract point toward centroid."""
    return [centroid[j] + rho * (point[j] - centroid[j]) for j in range(len(centroid))]


def _shrink(simplex: list[list[float]], best_idx: int, sigma: float = 0.5) -> list[list[float]]:
    """Shrink all vertices toward the best vertex."""
    best = simplex[best_idx]
    new_simplex = []
    for i, vertex in enumerate(simplex):
        if i == best_idx:
            new_simplex.append(list(vertex))
        else:
            new_simplex.append([
                best[j] + sigma * (vertex[j] - best[j])
                for j in range(len(vertex))
            ])
    return new_simplex


async def _optimization_loop(
    client: CSTClient,
    params_spec: list[dict],
    bands: list[dict],
    max_iterations: int,
    port: int,
) -> dict:
    """Run the Nelder-Mead optimization loop in connected mode.

    For each simplex vertex evaluation:
    1. Set parameters via execute_vba_silent (no history entry)
    2. Run solver via Python API run_solver() (no history entry)
    3. Export S11 via Python API export_result() (no history entry)
    4. Parse S11 and compute cost

    Only the final best-parameter application uses add_to_history.
    """
    n = len(params_spec)
    param_names = [p["name"] for p in params_spec]
    x0 = [p["initial"] for p in params_spec]
    bounds = [(p["min"], p["max"]) for p in params_spec]

    # Build initial simplex
    simplex = _build_initial_simplex(x0, bounds)
    costs: list[float] = []
    history: list[dict] = []

    # Delete any stale results up front to prevent dialog popups
    client.delete_results()

    # Start dialog watcher to auto-dismiss any popups during the loop
    client.start_dialog_watcher()

    # Temp file for S11 export
    work_dir = client._config.work_dir or tempfile.gettempdir()
    s11_file = os.path.join(work_dir, "_optim_s11_temp.csv").replace("\\", "/")

    # Helper: evaluate a parameter set
    eval_count = 0

    async def evaluate(x: list[float]) -> tuple[float, list[dict]]:
        nonlocal eval_count
        eval_count += 1

        # Clamp to bounds
        x = _clamp_to_bounds(x, bounds)
        params = dict(zip(param_names, x))

        # Use Python API: StoreParameter → DeleteResults → Rebuild → solve → export
        # This avoids history bloat and ensures the geometry actually rebuilds.
        result = client.set_params_rebuild_solve(params, export_path=s11_file, port=port)
        if result.get("status") == "error":
            logger.error("Solve iteration failed: %s", result.get("message"))
            return 100.0, []

        # Parse and evaluate
        try:
            freqs, s11_db = _parse_s11_data(s11_file)
            cost, band_results = _compute_cost(freqs, s11_db, bands)
            return cost, band_results
        except Exception as e:
            logger.error("Parse/eval error: %s", e)
            return 100.0, []

    # Evaluate initial simplex
    for vertex in simplex:
        cost, _ = await evaluate(vertex)
        costs.append(cost)

    # Track best
    best_idx = costs.index(min(costs))
    best_cost = costs[best_idx]
    best_params = dict(zip(param_names, simplex[best_idx]))

    history.append({
        "iteration": 0,
        "eval_count": eval_count,
        "best_cost": round(best_cost, 4),
        "best_params": {k: round(v, 4) for k, v in best_params.items()},
    })

    logger.info("Optimization start: cost=%.4f params=%s", best_cost, best_params)

    # Nelder-Mead iterations
    for iteration in range(1, max_iterations + 1):
        # Sort simplex by cost
        order = sorted(range(len(costs)), key=lambda i: costs[i])
        simplex = [simplex[i] for i in order]
        costs = [costs[i] for i in order]

        best_idx_local = 0
        worst_idx = len(simplex) - 1
        second_worst_idx = worst_idx - 1

        f_best = costs[best_idx_local]
        f_worst = costs[worst_idx]
        f_second_worst = costs[second_worst_idx]

        # Centroid of all except worst
        c = _centroid(simplex, worst_idx)

        # Reflect
        xr = _clamp_to_bounds(_reflect(c, simplex[worst_idx]), bounds)
        fr, _ = await evaluate(xr)

        if f_best <= fr < f_second_worst:
            # Accept reflection
            simplex[worst_idx] = xr
            costs[worst_idx] = fr
        elif fr < f_best:
            # Try expansion
            xe = _clamp_to_bounds(_expand(c, xr), bounds)
            fe, _ = await evaluate(xe)
            if fe < fr:
                simplex[worst_idx] = xe
                costs[worst_idx] = fe
            else:
                simplex[worst_idx] = xr
                costs[worst_idx] = fr
        else:
            # Contraction
            if fr < f_worst:
                # Outside contraction
                xc = _clamp_to_bounds(_contract(c, xr), bounds)
            else:
                # Inside contraction
                xc = _clamp_to_bounds(_contract(c, simplex[worst_idx]), bounds)
            fc, _ = await evaluate(xc)
            if fc < min(fr, f_worst):
                simplex[worst_idx] = xc
                costs[worst_idx] = fc
            else:
                # Shrink
                simplex = _shrink(simplex, best_idx_local)
                costs = []
                for vertex in simplex:
                    cost, _ = await evaluate(vertex)
                    costs.append(cost)

        # Update best
        current_best_idx = costs.index(min(costs))
        current_best_cost = costs[current_best_idx]
        if current_best_cost < best_cost:
            best_cost = current_best_cost
            best_params = dict(zip(param_names, simplex[current_best_idx]))

        history.append({
            "iteration": iteration,
            "eval_count": eval_count,
            "best_cost": round(best_cost, 4),
            "best_params": {k: round(v, 4) for k, v in best_params.items()},
        })

        logger.info(
            "Iter %d: cost=%.4f evals=%d params=%s",
            iteration, best_cost, eval_count, best_params,
        )

        # Convergence check: cost is 0 (all bands pass)
        if best_cost == 0.0:
            logger.info("All bands pass — converged at iteration %d", iteration)
            break

    # Apply best parameters + final solve + export
    # Use add_to_history for the final application so it's visible in the project
    final_params_vba = _set_params_vba(best_params)
    client.execute_vba(final_params_vba, history_label="optimization_best_params")

    # Then do a proper rebuild + solve + export via Python API
    client.set_params_rebuild_solve(best_params, export_path=s11_file, port=port)

    # Final evaluation
    try:
        freqs, s11_db = _parse_s11_data(s11_file)
        final_cost, final_bands = _compute_cost(freqs, s11_db, bands)
        resonances = _find_resonances(freqs, s11_db)
    except Exception:
        final_cost = best_cost
        final_bands = []
        resonances = []

    # Clean up temp file
    try:
        os.remove(s11_file)
    except OSError:
        pass

    # Stop dialog watcher and collect its log
    watcher_result = client.stop_dialog_watcher()
    dialog_log = watcher_result.get("log", [])

    overall = "PASS" if final_cost == 0.0 else "FAIL"

    result: dict = {
        "status": "optimized",
        "overall": overall,
        "best_cost": round(best_cost, 4),
        "best_params": {k: round(v, 4) for k, v in best_params.items()},
        "total_evaluations": eval_count,
        "iterations": len(history) - 1,
        "bands": final_bands,
        "resonances": resonances,
        "history": history,
    }
    if dialog_log:
        result["dismissed_dialogs"] = len(dialog_log)
        result["dialog_log"] = dialog_log

    return result


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _handle_evaluate(args: dict, client: CSTClient) -> dict:
    """Handle cst_evaluate_antenna."""
    bands = args["bands"]
    port = int(args.get("port", 1))

    if not client.connected or not client.has_project:
        # Offline mode: return VBA for manual evaluation
        script = VBAScript()
        script.add_comment("Export S-parameters for antenna evaluation")
        export_vba = _export_s11_vba("C:/cst_projects/s11_eval.csv", port)
        script.add_raw(export_vba)
        return {
            "status": "offline",
            "vba": script.build(),
            "message": (
                "Run this VBA in CST to export S11 data, then use the exported "
                "CSV to evaluate VSWR against your band targets manually."
            ),
            "bands": [
                {
                    "name": b["name"],
                    "f_low_ghz": b["f_low_ghz"],
                    "f_high_ghz": b["f_high_ghz"],
                    "vswr_target": b.get("vswr_target", 2.5),
                    "s11_threshold_db": round(vswr_to_s11(b.get("vswr_target", 2.5)), 2),
                }
                for b in bands
            ],
        }

    # Connected mode: export, parse, evaluate
    work_dir = client._config.work_dir or tempfile.gettempdir()
    s11_file = os.path.join(work_dir, "_eval_s11_temp.csv").replace("\\", "/")

    # Export S11 via Python API (no history entry, view-independent)
    tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
    result = client.export_result(tree_path, s11_file)
    if result.get("status") == "error":
        return {"status": "error", "message": f"Export failed: {result.get('message')}"}

    # Parse
    try:
        freqs, s11_db = _parse_s11_data(s11_file)
    except Exception as e:
        return {"status": "error", "message": f"Failed to parse S11 data: {e}"}

    # Evaluate
    band_results = _evaluate_bands(freqs, s11_db, bands)
    resonances = _find_resonances(freqs, s11_db)

    overall = "PASS" if all(b["status"] == "PASS" for b in band_results) else "FAIL"

    # Clean up
    try:
        os.remove(s11_file)
    except OSError:
        pass

    return {
        "status": "evaluated",
        "overall": overall,
        "bands": band_results,
        "resonances": resonances,
        "frequency_range_ghz": [round(freqs[0], 4), round(freqs[-1], 4)],
        "num_points": len(freqs),
    }


async def _handle_refine(args: dict, client: CSTClient) -> dict:
    """Handle cst_refine_antenna."""
    params_spec = args["parameters"]
    bands = args["bands"]
    max_iterations = int(args.get("max_iterations", 20))
    port = int(args.get("port", 1))

    # Validate
    for p in params_spec:
        if p["min"] >= p["max"]:
            return {
                "status": "error",
                "message": f"Parameter '{p['name']}' min ({p['min']}) must be < max ({p['max']})",
            }
        if not (p["min"] <= p["initial"] <= p["max"]):
            return {
                "status": "error",
                "message": (
                    f"Parameter '{p['name']}' initial ({p['initial']}) "
                    f"must be within [{p['min']}, {p['max']}]"
                ),
            }

    if max_iterations < 1:
        return {"status": "error", "message": "max_iterations must be >= 1"}

    if not client.connected or not client.has_project:
        # Offline: fall back to CST built-in optimizer VBA
        script = VBAScript()
        script.add_comment("Antenna optimization via CST built-in optimizer")

        vba = VBABuilder("Optimizer").call("Reset")
        vba.set("SetOptimizerType", "Nelder Mead")
        vba.set_number("SetMaxEvaluations", max_iterations * (len(params_spec) + 1))

        # Use first band's target for the goal
        first_band = bands[0]
        s11_threshold = vswr_to_s11(first_band.get("vswr_target", 2.5))
        tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
        vba.set("SetGoalType", "Min")
        vba.set("SetGoalResult", tree_path)
        vba.call("InitGoal")

        for p in params_spec:
            vba.call_with_args("AddParameter", p["name"], str(p["min"]), str(p["max"]))

        vba.call("Start")
        script.add_block(vba)

        return {
            "status": "offline",
            "vba": script.build(),
            "message": (
                "Run this VBA in CST to optimize using the built-in optimizer. "
                "In connected mode, the MCP server runs a custom Nelder-Mead loop "
                "with per-band VSWR cost evaluation."
            ),
        }

    # Connected mode: run optimization loop
    return await _optimization_loop(client, params_spec, bands, max_iterations, port)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, callable] = {
    "cst_evaluate_antenna": _handle_evaluate,
    "cst_refine_antenna": _handle_refine,
}


def _text(data: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(data, indent=2))]


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle an optimization tool call."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _text({"status": "error", "message": f"Unknown optimization tool: {name}"})

    try:
        result = await handler(arguments, client)
        return _text(result)
    except Exception as e:
        return _text({"status": "error", "message": str(e)})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_optimization_tools(server: Server, client: CSTClient) -> None:
    """Register optimization tools with the MCP server."""
    from mcp_cst_studio.tools import _registry
    _registry.add_module(TOOLS, handle, client)
