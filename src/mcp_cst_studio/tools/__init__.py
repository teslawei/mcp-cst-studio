"""Tool registration aggregator for CST Studio MCP server.

Each tool module exposes:
  - ``TOOLS``: list of ``mcp.types.Tool`` definitions
  - ``handle(name, arguments, client)``: async dispatcher
  - ``register_*_tools(server, client)``: convenience to add both

The ``ToolRegistry`` collects tools and handlers from every module,
then wires them into the MCP server via explicit ``add_request_handler()``
registrations for the tools/list and tools/call protocol handlers.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from mcp.types import CallToolRequestParams, CallToolResult, ListToolsRequest, ListToolsResult, TextContent, Tool

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mcp.server import Server

    from mcp_cst_studio.cst_client import CSTClient

# Type alias for an async tool handler
ToolHandler = Callable[[str, dict], Awaitable[list[TextContent]]]


class ToolRegistry:
    """Accumulates Tool definitions and their handlers across modules."""

    def __init__(self) -> None:
        self._tools: list[Tool] = []
        self._handlers: dict[str, ToolHandler] = {}

    def clear(self) -> None:
        """Remove all registered tools and handlers."""
        self._tools.clear()
        self._handlers.clear()

    # -- public API used by each register_*_tools function --

    def add_tool(self, tool: Tool, handler: ToolHandler) -> None:
        """Register a single tool definition with its handler."""
        self._tools.append(tool)
        self._handlers[tool.name] = handler

    def add_module(
        self,
        tools: list[Tool],
        handle_fn: Callable[..., Awaitable[list[TextContent]]],
        client: CSTClient,
    ) -> None:
        """Register an entire tool module (TOOLS list + handle function)."""
        for tool in tools:
            self._tools.append(tool)
            # Bind *client* at registration time so each call gets the right ref
            self._handlers[tool.name] = cast(
                ToolHandler,
                lambda name, args, _h=handle_fn, _c=client: _h(name, args, _c),
            )

    # -- internal: wire into the MCP server --

    def install(self, server: Server) -> None:
        """Register the tools/list and tools/call MCP protocol handlers."""
        tools = list(self._tools)
        handlers = dict(self._handlers)

        async def handle_list_tools(
            ctx: object, params: ListToolsRequest
        ) -> ListToolsResult:
            """List all registered tools."""
            return ListToolsResult(tools=tools)

        async def handle_call_tool(
            ctx: object, params: CallToolRequestParams
        ) -> CallToolResult:
            """Call a tool by name with the provided arguments."""
            name = params.name
            arguments = params.arguments
            handler = handlers.get(name)
            if handler is None:
                raise ValueError(f"Unknown tool: {name}")
            logger.debug("Dispatching tool: %s", name)
            content = await handler(name, arguments)
            return CallToolResult(content=content)

        server.add_request_handler("tools/list", ListToolsRequest, handle_list_tools)
        server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)


# Module-level registry shared across register_* calls
_registry = ToolRegistry()


def register_all_tools(server: Server, client: CSTClient) -> None:
    """Register all tool modules with the MCP server."""
    _registry.clear()  # prevent duplicate registration on repeated calls
    from mcp_cst_studio.tools.antenna_templates import register_antenna_template_tools
    from mcp_cst_studio.tools.arrays import register_array_tools
    from mcp_cst_studio.tools.boolean import register_boolean_tools
    from mcp_cst_studio.tools.boundaries import register_boundary_tools
    from mcp_cst_studio.tools.diagnostics import register_diagnostics_tools
    from mcp_cst_studio.tools.geometry import register_geometry_tools
    from mcp_cst_studio.tools.import_export import register_import_export_tools
    from mcp_cst_studio.tools.matching import register_matching_tools
    from mcp_cst_studio.tools.materials import register_material_tools
    from mcp_cst_studio.tools.mesh import register_mesh_tools
    from mcp_cst_studio.tools.optimization import register_optimization_tools
    from mcp_cst_studio.tools.parameters import register_parameter_tools
    from mcp_cst_studio.tools.pcb import register_pcb_tools
    from mcp_cst_studio.tools.ports import register_port_tools
    from mcp_cst_studio.tools.project import register_project_tools
    from mcp_cst_studio.tools.results import register_result_tools
    from mcp_cst_studio.tools.simulation import register_simulation_tools
    from mcp_cst_studio.tools.solvers import register_solver_tools
    from mcp_cst_studio.tools.transforms import register_transform_tools
    from mcp_cst_studio.tools.vba import register_vba_tools

    register_project_tools(server, client)
    register_geometry_tools(server, client)
    register_boolean_tools(server, client)
    register_transform_tools(server, client)
    register_material_tools(server, client)
    register_port_tools(server, client)
    register_boundary_tools(server, client)
    register_mesh_tools(server, client)
    register_solver_tools(server, client)
    register_simulation_tools(server, client)
    register_result_tools(server, client)
    register_import_export_tools(server, client)
    register_parameter_tools(server, client)
    register_optimization_tools(server, client)
    register_diagnostics_tools(server, client)
    register_antenna_template_tools(server, client)
    register_array_tools(server, client)
    register_pcb_tools(server, client)
    register_matching_tools(server, client)
    register_vba_tools(server, client)

    # Wire accumulated tools into the MCP server protocol
    _registry.install(server)
