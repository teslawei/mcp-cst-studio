# Changelog

All notable changes to **mcp-cst-studio** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **STP-side OCC simplification module** (`tools/cad.py`, 2 tools,
  optional extra `pip install cadquery-ocp`):
  `cst_stp_recon` (product→solid enumeration with exact bounding boxes
  and volumes via the XCAF tree) and `cst_stp_simplify` (whitelist prune
  + ShapeFix + fuzzy boolean fuse + BRepCheck-validated AP214 export).
  Pure file processing — no running CST required. Validated end-to-end on
  a 167-product / 1370-solid battery assembly reduced to 5 simulation
  solids, including a housing seam fuse that ACIS refuses twice on clean
  geometry.
- **`skills/cst-model-simplification/SKILL.md`**: the field-tested
  model-simplification workflow (STP-first division of labor, CST-only
  fallback route, OCP 8 binding bugs, CST history/cache traps, and the
  full verification checklist) for reuse across projects.
- `pyproject.toml` optional dependency group `cad`
  (`cadquery-ocp>=8.0`); tests `tests/test_tools_cad.py` run offline and
  exercise the real recon path when OCP is installed.
- **History & recovery tool module** (`tools/history.py`, 4 tools):
  `cst_full_history_rebuild` (in-place replay of the complete modeler
  history — restores geometry deleted by later history entries without
  restarting CST), `cst_list_tree_items` (authoritative tree enumeration
  via the documented `get_tree_items()` API — unlike `SelectTreeItem`
  probing it never yields false positives), `cst_solid_count`, and
  `cst_execute_vba_query` (marker-file bridge that returns values from
  read-only VBA expressions, which `add_to_history` cannot do natively).
- `CSTClient.full_history_rebuild()`, `CSTClient.get_tree_items()`,
  `CSTClient.execute_vba_query()`; `execute_vba` now forwards a
  caller-supplied `timeout` to `add_to_history` (modal error boxes can
  block the COM call indefinitely otherwise).
- `docs/HISTORY_AND_RECOVERY.md`: field-tested methodology for history
  replay, crash rollback semantics, dialog traps, and geometry-audit
  pitfalls (loose bounding boxes are control-point hulls, not containment
  proofs).
- Dialog detection: bare `VBA`-titled `#32770` error boxes and the Qt
  `CST MICROWAVE STUDIO 202x` RemoteUI hosts are now matched even when
  process-based detection fails.

## [0.2.0]: 2026-05-13

### Changed
- **License: Apache-2.0 → AGPL-3.0-or-later.** Aligns with the
  eng-mcp-suite toolkit-wide AGPL move. CST Studio Suite itself
  (Dassault Systèmes) is a separate commercial product invoked at
  runtime; this wrapper does not bundle or redistribute it. See the
  [LICENSE_SUMMARY](https://github.com/RFingAdam/eng-mcp-suite/blob/main/LICENSE_SUMMARY.md)
  for the toolkit-wide rationale.

## [0.1.0]

Initial release. MCP server wrapper for CST Studio Suite (Dassault
Systèmes): antenna design, RF simulation, PCB layout. Requires a
licensed CST Studio installation.
