---
name: cst-model-simplification
description: Field-tested workflow for simplifying large CAD assemblies (battery packs, enclosures) for CST Studio Suite simulation - prune components, fuse mating sections via OpenCascade when ACIS refuses, import into CST, assign PEC, and verify. Use when asked to simplify/clean a model, delete components, unite solids, or build a simulation-ready variant of a STEP file.
---

# CST Model Simplification Workflow (STP-first)

Purpose: turn a huge STEP assembly (hundreds of products, thousands of
solids) into a small simulation-ready CST project **reproducibly**, without
the failure modes that make CST-side editing slow and fragile.

Proven on: `F:\bp_simplified.stp` (167 products / 1370 solids -> 5 solids,
single fused housing) in `D:\Project\Simulation\CST`.

## Division of labor (the core decision)

| Work | Do it HERE | Why |
|---|---|---|
| component pruning, unite/heal, geometry cleanup | **STP side (OCC)** | deterministic, no dialogs, no history traps, exact bboxes, free retries |
| import, PEC/material, EMC layer, mesh, solve | **CST side** | only CST owns these |

CST-side `Solid.Add` may refuse mating seams with `Error in boolean
operation` / `-2147418113` even on clean geometry (verified twice on the
same seam). OCC's `BRepAlgoAPI_Fuse` with a small fuzzy value handles those
seams. Do not burn time fighting ACIS.

## Route A - OCC pipeline (preferred)

### A1. Recon: enumerate products with names

MCP tools (offline-capable, pure file processing):

```
cst_stp_recon  {source_stp}                      # product->solid map + exact bbox + volume
cst_stp_simplify {source_stp, output_stp,
                  whitelist:{role:path_suffix},
                  fuse_roles:[...], cache_dir}   # prune + fix + fuse + export AP214
```

Requires `pip install cadquery-ocp` (OCP 8.x, Windows wheels exist for
py3.9-3.12).

Standalone equivalent lives in `dsh_work/stp_simplify3.py` (session
workspace) and follows this exact sequence.

### A2. Build the whitelist

- Walk the XCAF tree from the single root, resolving references
  (`IsReference_s` -> `GetReferredShape_s`); the path is the
  underscore-joined product names, e.g.
  `6543210-00-A_0_6543211-00-A_1_2082726-00-A`.
- Whitelist entries are **path suffixes** matched with `endswith`. Roles
  map to the kept assemblies (e.g. `housing`, `base`, `platters`).
- Verify each role in recon output: solid count and bbox must match the
  model you expect (exact `CornerMin/CornerMax` boxes - see OCP traps).

### A3. Simplify (the pipeline order matters)

1. Read with `STEPCAFControl_Reader`, `SetNameMode(True)`.
2. Keep ONLY solids under whitelist paths (this one step replaces the
   historical ~100 `Component.Delete` calls).
3. `ShapeFix_Shape` every input solid; keep only if `BRepCheck_Analyzer`
   says valid after fix (else keep original and report).
4. Fuse the target role's solids: `SetFuzzyValue(1e-4)` (mm) **before**
   `Build()`. Without fuzzy, tangent-seam fuses come out INVALID.
5. Assert the fuse result is ONE valid solid
   (`TopExp_Explorer` count + `BRepCheck_Analyzer`). Abort on failure -
   do not export invalid geometry.
6. Write AP214 with `STEPCAFControl_Writer` (schema via
   `Interface_Static.SetCVal_s('write.step.schema','AP214')`), one named
   product per role. Round-trip re-read to verify product/solid counts.

Expect ~80 s to read a 500 MB STP and ~50 s per fuse; cache extracted
roles as `.brep` for fast iteration.

#### Full-unite recipes (every part into ONE solid)

For "unite the whole assembly" requests, fuse pairwise with a
**(fuzzy x repair) ladder per stage** and accept the first
`n=1 and BRepCheck_Analyzer.IsValid()` result:

- repair ladder per fuzzy: raw -> `UnifySameDomain` ->
  `ShapeFix_Shape(usd)` -> `ShapeFix_Shape(raw)`
- fuzzy escalation per stage: `1e-4 -> 1e-3 -> 5e-3`
- **coplanar/tangent interfaces** (housing flange on a tray): needed
  `fz=5e-3 + USD + ShapeFix` to come out valid (fz<=1e-3 stayed invalid).
- **flat contacts fuse clean** (platters onto floor at `1e-4`, ~10 s each).
- **tangent face-to-face contact fuses never go valid at any fuzzy**
  (EMC shield sitting ON a curved housing surface: raw/usd/sfix all
  invalid at 1e-4..5e-3, and fz>=1e-2 aborts). Fix: translate the
  mating part **0.1-0.2 mm deeper** to turn tangent contact into an
  explicit overlap - then the same fuse goes VALID at 1e-4 raw. For PEC
  parts this is electrically and visually identical to touching.
- Order matters: fuse flat/simple contacts first, tangential pairs
  early while the accumulated solid is small, and probe 2-3 offsets.
- Keep a valid intermediate `.brep` before every risky stage.
- OCP 8.0.1 trims `TopTools` (`TopTools_ListOfShape` missing), so
  `BOPAlgo_Builder` multi-argument general fuse is unavailable; pairwise
  chains are the way.

#### Pulling parts from a SECOND STP (e.g. an EMC release file)

- Recon the second file the same way (`GetShapes` + names + per-solid
  bbox); product names are usually part numbers (`3DP-27288759-A`).
- Coordinate frames typically MATCH the main assembly - verify by
  checking the part's bbox sits where the model expects it before
  fusing.

### A4. CST import (new project)

Run under CST's bundled Python 3.9
(`C:\Program Files (x86)\CST Studio Suite 2024\Opera\code\bin\python.exe`)
with `sys.path` pointing at `...\AMD64` + `...\AMD64\python_cst_libraries`:

```python
de = ci.DesignEnvironment.connect(ci.running_design_environments()[0])
de.set_quiet_mode(True)
p = de.new_mws()
m = p.model3d
safe_history(m, 'import_v2', "With STEP\n  .Reset\n  .FileName \"<clean.stp>\"\n"
             "  .Id \"3\"\n  .Healing \"True\"\n  .ImportAttributes \"True\"\n"
             "  .Read\nEnd With\n", timeout=1800)
items = m.get_tree_items(timeout=300)      # documented API - never false positives
```

- Every VBA goes through the `cst_safe.safe_history` wrapper (dialog
  watcher mandatory; passing `timeout` is required).
- Solids land in `Components\default\<solid>`; the full solid name is
  `default:<tree-name>` (tree paths use backslashes and contain NO colon).
- PEC with err-code logging through the marker-file bridge:

```vba
Solid.ChangeMaterial "default:<solid>", "PEC"   ' expect Err 0
Solid.GetNumberOfShapes                          ' expect = role total
```

- Save with `p.save(path)`; if that fails use `m.Save()` (see traps).
- Acceptance test: per-solid `GetLooseBoundingBoxOfShape` vs the source
  bbox table (control-hull vs control-hull comparisons are valid; never
  infer containment from them).

## Route B - CST-only editing (small changes on an existing project)

Use only for edits that are cheap in CST (single boolean, rename,
material). Ground rules:

- One logical operation per `add_to_history` entry (atomic rollback).
- Existence checks ONLY via `model3d.get_tree_items()` or delete return
  codes (`0` deleted, `-2147418113` refused/absent). `SelectTreeItem`
  returns 0 for non-existent names - useless as an oracle.
- Never infer containment from `GetLooseBoundingBoxOfShape` (control-point
  hull).
- Deleting history entries = JSON surgery on
  `Model/3D/ModelHistory.json` + hide `Model.sab/.alc/.bwc` caches +
  fresh process. Prefer in-place `model3d.full_history_rebuild()`.
- Recovery: see `docs/HISTORY_AND_RECOVERY.md`.

## Hard-won traps (do not relearn them)

- **ACIS refuses valid seams** (`-2147418113`) even on clean geometry;
  heal/imprint does not fix it. Go OCC.
- **OCP 8.0.1 binding bugs**: `Bnd_Box.Get()` raises "Unregistered type"
  -> use `CornerMin()/CornerMax()` (the box IS filled even when `Add_s`
  raises a return-conversion TypeError); `TopoDS.Compound()` has no
  default ctor -> `TopoDS_Shape()` + `BRepTools.Read_s(shape, file,
  builder)`; `AddOptimal_s` 4-arg overload broken -> 2-arg or `Add_s`;
  `TopTools_ListOfShape` absent -> no `BOPAlgo_Builder` general fuse.
- **Unnamed STEP files list instances AND definitions** in `GetShapes`
  (same geometry twice) - dedupe recon rows by `(bbox, volume)`.
- **XCAF**: `GetShapes` = top-level products (may be duplicate-named
  instances elsewhere in the file - always verify via the tree walk path,
  not the label name); `GetShape_s` on component labels works, but on
  deeper leaves resolve references first.
- **CST same-process close/reopen does NOT replay history**; use
  `full_history_rebuild()`. `IsBuildingModel()` may stay True after a
  rebuild (stale flag) - trust solid counts, save via `m.Save()`.
- **Never call `Rebuild` inside an `add_to_history` entry** ("cannot be
  used inside a structure macro").
- **`GetLooseBoundingBoxOfShape` can be wildly wrong** on huge
  B-spline solids (reported z-max 1163.8 vs true 650.6 on a fused
  battery pack) - it is a control-point hull; use OCC-side bboxes for
  truth and CST loose boxes only for same-process comparisons.
- WMI process launch and `git push` need sandbox escalation
  (`danger-full-access`); git needs `-c http.sslBackend=openssl`.
- py3.9 f-strings cannot contain backslashes in the expression part.

## Verification checklist (every simplification)

1. `cst_stp_recon` bbox/volume table matches expectation per kept role.
2. Fuse result: single solid + `BRepCheck` valid + exported STP
   round-trips with expected product/solid counts.
3. CST: `Solid.GetNumberOfShapes` == expected; PEC err codes all `0`;
   per-solid loose bboxes match source hulls; project saved.
