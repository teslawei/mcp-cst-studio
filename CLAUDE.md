# CST Studio MCP Server — Development Guide

## Architecture

- **Dual-mode**: Connected (Windows+CST) and Offline (any OS, generates VBA scripts)
- **VBA Builder pattern**: All VBA generated via `vba_builder.py` — never use raw f-strings
- **Modular tools**: Each `tools/*.py` exports `TOOLS` list + `handle()` function + `register_*_tools()`
- **ToolRegistry**: `tools/__init__.py` collects all tools and wires single `list_tools`/`call_tool` handlers

## Key Files

- `types.py` — Enums and dataclasses (ProjectType, SolverType, etc.)
- `config.py` — Env vars, CST path auto-detection
- `validators.py` — Input validation, VBA injection prevention
- `vba_builder.py` — Safe VBA code generation (builder pattern)
- `cst_client.py` — Session manager (connected/offline modes)
- `server.py` — MCP entry point
- `tools/` — 16 tool modules (~107 tools total)
- `data/` — JSON material databases, antenna templates, VBA reference

## Adding New Tools

1. Add Tool definition to the module's `TOOLS` list
2. Add handler in the module's `handle()` function
3. Use `VBABuilder` for all VBA generation
4. Validate all inputs via `validators.py`
5. Return `list[TextContent]` with JSON response

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Tests run in offline mode — no CST installation needed.

## Security Rules

- All VBA passes through `validate_vba_input()` — blocks Shell, CreateObject, file I/O
- All file paths checked for traversal (`..`)
- All names restricted to alphanumeric + underscore + space + dot + hyphen
- No hardcoded paths — everything via env vars or auto-detection

## Build & Run

```bash
pip install -e .
mcp-cst-studio  # runs stdio server
```

Environment variables:
- `CST_PATH` — CST installation directory
- `CST_WORK_DIR` — Working directory for projects
- `CST_VERSION` — CST version (default: 2026)
