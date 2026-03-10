"""CST Studio connection manager with connected/offline modes."""

from __future__ import annotations

import logging
from typing import Any

from mcp_cst_studio.config import CSTConfig

logger = logging.getLogger(__name__)

# Try importing the official CST Python library
try:
    import cst.interface  # type: ignore[import-untyped]
    import cst.results  # type: ignore[import-untyped]
    CST_AVAILABLE = True
except ImportError:
    CST_AVAILABLE = False


class CSTClient:
    """Manages CST Studio Suite connection.

    In connected mode (Windows with CST), executes VBA directly.
    In offline mode (any OS), returns generated VBA scripts.
    """

    def __init__(self, config: CSTConfig | None = None) -> None:
        self._config = config or CSTConfig.from_env()
        self._de: Any = None  # cst.interface.DesignEnvironment
        self._project: Any = None  # Active project handle
        self._project_path: str | None = None

    @property
    def connected(self) -> bool:
        return self._config.connected and CST_AVAILABLE

    @property
    def has_project(self) -> bool:
        return self._project is not None

    @property
    def project_path(self) -> str | None:
        return self._project_path

    @property
    def mode(self) -> str:
        return "connected" if self.connected else "offline"

    def connect(self) -> dict:
        """Connect to CST Design Environment."""
        if not CST_AVAILABLE:
            return {
                "status": "offline",
                "message": "CST Python library not available. Running in offline mode — "
                "VBA scripts will be generated but not executed.",
            }

        try:
            self._de = cst.interface.DesignEnvironment()
            self._config.connected = True
            return {"status": "connected", "message": "Connected to CST Design Environment"}
        except Exception as e:
            self._config.connected = False
            return {
                "status": "offline",
                "message": f"Failed to connect to CST: {e}. Running in offline mode.",
            }

    def disconnect(self) -> dict:
        """Disconnect from CST."""
        if self._de is not None:
            try:
                self._de.close()
            except Exception:
                pass
            self._de = None
            self._project = None
            self._project_path = None
            self._config.connected = False
        return {"status": "disconnected"}

    def new_project(self, path: str, project_type: str = "MWS") -> dict:
        """Create a new CST project."""
        if self.connected and self._de is not None:
            try:
                self._project = self._de.new_mws()
                self._project.save(path)
                self._project_path = path
                return {"status": "created", "path": path, "type": project_type}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {
            "status": "offline",
            "path": path,
            "type": project_type,
            "message": "Project creation requires connected mode. "
            "Use the generated VBA scripts on a Windows machine with CST.",
        }

    def open_project(self, path: str) -> dict:
        """Open an existing CST project."""
        if self.connected and self._de is not None:
            try:
                self._project = self._de.open_project(path)
                self._project_path = path
                return {"status": "opened", "path": path}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        self._project_path = path
        return {
            "status": "offline",
            "path": path,
            "message": "Project opened in offline reference mode.",
        }

    def save_project(self, path: str | None = None) -> dict:
        """Save the current project."""
        save_path = path or self._project_path
        if self.connected and self._project is not None:
            try:
                if save_path:
                    self._project.save(save_path)
                else:
                    self._project.save()
                self._project_path = save_path
                return {"status": "saved", "path": save_path}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {"status": "offline", "message": "Save requires connected mode."}

    def close_project(self) -> dict:
        """Close the current project."""
        if self.connected and self._project is not None:
            try:
                self._project.close()
            except Exception:
                pass
            self._project = None
            self._project_path = None
            return {"status": "closed"}

        self._project = None
        self._project_path = None
        return {"status": "closed", "message": "Project reference cleared."}

    def execute_vba(self, vba_code: str) -> dict:
        """Execute VBA code in CST.

        In connected mode: executes directly and returns result.
        In offline mode: returns the VBA script for manual execution.
        """
        if self.connected and self._project is not None:
            try:
                result = self._project.schematic.execute_vba_code(vba_code)
                return {"status": "executed", "result": str(result) if result else "ok"}
            except Exception as e:
                return {"status": "error", "message": str(e), "vba": vba_code}

        return {
            "status": "offline",
            "vba": vba_code,
            "message": "VBA script generated. Execute in CST Studio Suite on Windows.",
        }

    def get_result(self, tree_path: str) -> dict:
        """Get a result from the CST result tree."""
        if self.connected and self._project is not None:
            try:
                result = cst.results.ProjectFile(self._project_path)
                data = result.get_3d().get_tree_item(tree_path)
                return {"status": "ok", "data": str(data)}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {
            "status": "offline",
            "tree_path": tree_path,
            "message": "Result retrieval requires connected mode with a completed simulation.",
        }

    def status(self) -> dict:
        """Get current client status."""
        return {
            "mode": self.mode,
            "cst_available": CST_AVAILABLE,
            "cst_path": self._config.cst_path,
            "cst_version": self._config.version,
            "work_dir": self._config.work_dir,
            "project_open": self.has_project,
            "project_path": self._project_path,
        }
