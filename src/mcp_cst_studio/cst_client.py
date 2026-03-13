"""CST Studio connection manager with connected/offline modes."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from mcp_cst_studio.config import CSTConfig
from mcp_cst_studio.dialog_handler import DialogWatcher, dismiss_cst_dialogs, find_cst_dialogs

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
        """Connect to CST Design Environment.

        First tries to connect to an already-running instance. If none
        is running, launches a new one.
        """
        if not CST_AVAILABLE:
            return {
                "status": "offline",
                "message": "CST Python library not available. Running in offline mode — "
                "VBA scripts will be generated but not executed.",
            }

        try:
            # Try connecting to an already-running CST instance first
            running = cst.interface.running_design_environments()
            if running:
                self._de = cst.interface.DesignEnvironment.connect(running[0])
                self._config.connected = True
                # Pick up any already-open project
                open_projects = self._de.get_open_projects()
                if open_projects:
                    self._project = open_projects[0]
                    fname = self._project.filename
                    self._project_path = str(fname() if callable(fname) else fname)
                return {
                    "status": "connected",
                    "message": f"Connected to running CST instance (PID {running[0]})",
                    "open_projects": len(open_projects) if open_projects else 0,
                }

            # No running instance — launch a new one
            self._de = cst.interface.DesignEnvironment()
            self._config.connected = True
            return {"status": "connected", "message": "Launched new CST Design Environment"}
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

    _history_counter: int = 0

    def execute_vba(self, vba_code: str, history_label: str | None = None) -> dict:
        """Execute VBA code in CST.

        In connected mode: executes via ``model3d.add_to_history()`` which
        adds the VBA macro to the project history and runs it immediately.
        In offline mode: returns the VBA script for manual execution.
        """
        if self.connected and self._project is not None:
            try:
                CSTClient._history_counter += 1
                label = history_label or f"mcp_action_{CSTClient._history_counter}"
                result = self._project.model3d.add_to_history(label, vba_code)
                return {"status": "executed", "result": str(result) if result else "ok"}
            except Exception as e:
                return {"status": "error", "message": str(e), "vba": vba_code}

        return {
            "status": "offline",
            "vba": vba_code,
            "message": "VBA script generated. Execute in CST Studio Suite on Windows.",
        }

    def execute_vba_silent(self, vba_code: str) -> dict:
        """Execute VBA without adding to project history.

        Uses ``schematic.execute_vba_code()`` which runs the macro silently.
        The code must be wrapped in ``Sub Main() ... End Sub``.
        Ideal for optimization loops where dozens of iterations would
        otherwise bloat the history list.

        In offline mode: returns the VBA script for manual execution.
        """
        if self.connected and self._project is not None:
            try:
                self._project.schematic.execute_vba_code(vba_code)
                return {"status": "executed"}
            except Exception as e:
                return {"status": "error", "message": str(e), "vba": vba_code}

        return {
            "status": "offline",
            "vba": vba_code,
            "message": "VBA script generated (silent). Execute in CST Studio Suite.",
        }

    def is_solver_running(self) -> bool:
        """Check if a solver is currently running."""
        if self.connected and self._project is not None:
            try:
                return bool(self._project.model3d.is_solver_running())
            except Exception:
                return False
        return False

    def wait_for_solver(self, timeout: float = 600, poll_interval: float = 2.0) -> dict:
        """Wait for a running solver to finish.

        Returns immediately if no solver is running.
        """
        if not self.connected or self._project is None:
            return {"status": "offline"}

        deadline = time.monotonic() + timeout
        while self.is_solver_running():
            if time.monotonic() > deadline:
                return {"status": "error", "message": f"Solver still running after {timeout}s"}
            time.sleep(poll_interval)

        return {"status": "ok"}

    def run_solver(self) -> dict:
        """Run the solver via Python API (no VBA, no history entry).

        Uses ``model3d.run_solver()`` which blocks until complete.
        If a solver is already running, waits for it to finish first.
        """
        if self.connected and self._project is not None:
            try:
                # Wait for any in-progress solver before starting
                if self.is_solver_running():
                    logger.info("Solver already running — waiting for it to finish")
                    wait_result = self.wait_for_solver()
                    if wait_result.get("status") == "error":
                        return wait_result

                result = self._project.model3d.run_solver()
                return {"status": "executed", "result": str(result) if result else "ok"}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {"status": "offline", "message": "Solver requires connected mode."}

    def export_result(self, tree_path: str, filepath: str) -> dict:
        """Export a result tree item to CSV via Python API (no history entry).

        Uses ``model3d.SelectTreeItem()`` + ``model3d.ASCIIExport`` Python
        methods directly — avoids VBA and history bloat.  Works regardless of
        the current CST view state.
        """
        if self.connected and self._project is not None:
            try:
                m3d = self._project.model3d
                m3d.SelectTreeItem(tree_path)
                ae = m3d.ASCIIExport
                ae.Reset()
                ae.FileName(filepath.replace("\\", "/"))
                ae.SetFileType("csv")
                ae.Execute()
                return {"status": "exported", "path": filepath}
            except Exception as e:
                return {"status": "error", "message": str(e)}

        return {
            "status": "offline",
            "tree_path": tree_path,
            "message": "Result export requires connected mode.",
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

    def delete_results(self) -> dict:
        """Delete solver results via ``model3d.DeleteResults()``.

        This is critical before rebuilding with new parameters — without
        deleting results first, the solver may return cached/stale data
        even after a ``Rebuild()``.

        Also dismisses any CST dialogs that may appear.
        """
        if not self.connected or self._project is None:
            return {"status": "offline", "message": "Delete results requires connected mode."}

        try:
            self._project.model3d.DeleteResults()
        except Exception as e:
            logger.warning("DeleteResults error (non-fatal): %s", e)

        # Dismiss any dialogs that may have appeared
        dismissed = self.dismiss_dialogs()
        return {"status": "ok", "method": "python_api", **dismissed}

    def set_params_rebuild_solve(
        self,
        params: dict[str, float],
        export_path: str | None = None,
        port: int = 1,
    ) -> dict:
        """Set parameters, rebuild geometry, solve, and optionally export S11.

        Uses the Python API directly (no VBA, no history entries).  The
        correct sequence to get fresh results after a parameter change is:

        1. ``StoreParameter`` — update parameter table
        2. ``DeleteResults`` — clear cached solver results
        3. ``Rebuild`` — rebuild geometry from history with new values
        4. ``run_solver`` — run a fresh simulation
        5. (optional) ``ASCIIExport`` — export S-parameter data

        Without ``DeleteResults`` before ``Rebuild``, the solver returns
        stale cached data even though the parameter values have changed.

        Returns dict with status and optional export path.
        """
        if not self.connected or self._project is None:
            return {"status": "offline", "message": "Requires connected mode."}

        m3d = self._project.model3d

        try:
            # 1. Store parameters
            for name, value in params.items():
                m3d.StoreParameter(name, str(value))

            # 2. Delete old results (critical!)
            m3d.DeleteResults()

            # 3. Dismiss any dialogs
            self.dismiss_dialogs()

            # 4. Rebuild geometry
            m3d.Rebuild()

            # 5. Dismiss any post-rebuild dialogs
            self.dismiss_dialogs()

            # 6. Run solver
            if self.is_solver_running():
                wait_result = self.wait_for_solver()
                if wait_result.get("status") == "error":
                    return wait_result
            m3d.run_solver()

            # 7. Export if requested
            if export_path:
                import os
                if os.path.exists(export_path):
                    os.remove(export_path)
                tree_path = f"1D Results\\S-Parameters\\S{port},{port}"
                m3d.SelectTreeItem(tree_path)
                ae = m3d.ASCIIExport
                ae.Reset()
                ae.FileName(export_path.replace("\\", "/"))
                ae.SetFileType("csv")
                ae.Execute()

            return {"status": "ok", "params": params, "export_path": export_path}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def read_project_messages(self) -> dict:
        """Read solver log and project status information.

        Scans the project results directory for log files and returns
        their contents along with current solver state.
        """
        info: dict[str, Any] = {"solver_running": False}

        if not self.connected or self._project is None:
            return {"status": "offline", "message": "Requires connected mode."}

        info["solver_running"] = self.is_solver_running()
        info["project_path"] = self._project_path

        if not self._project_path:
            return {"status": "ok", **info}

        # CST stores results in a directory alongside the .cst file
        # e.g. "Project.cst" -> "Project/Result/"
        project_base = self._project_path.replace(".cst", "")
        candidate_dirs = [
            os.path.join(project_base, "Result"),
            project_base,
        ]

        log_files: list[str] = []
        for d in candidate_dirs:
            if os.path.isdir(d):
                try:
                    for fname in os.listdir(d):
                        lower = fname.lower()
                        if "log" in lower or "solver" in lower or lower.endswith(".log"):
                            log_files.append(os.path.join(d, fname))
                except OSError:
                    continue

        if log_files:
            info["log_files"] = log_files
            try:
                newest = max(log_files, key=os.path.getmtime)
                with open(newest, "r", errors="replace") as f:
                    content = f.read()
                # Return last 5000 chars to keep response manageable
                info["latest_log"] = content[-5000:] if len(content) > 5000 else content
                info["latest_log_file"] = newest
            except OSError:
                pass

        return {"status": "ok", **info}

    # -- dialog management --

    _dialog_watcher: DialogWatcher | None = None

    def dismiss_dialogs(self) -> dict:
        """Find and dismiss any visible CST dialog windows.

        Returns details of each dialog that was dismissed (title, text,
        action taken).  Uses Win32 API on Windows; no-op on other platforms.
        """
        dismissed = dismiss_cst_dialogs()
        if dismissed:
            return {"status": "dismissed", "count": len(dismissed), "dialogs": dismissed}
        return {"status": "ok", "message": "No CST dialogs found."}

    def read_dialogs(self) -> dict:
        """Read (but don't dismiss) any visible CST dialog windows."""
        dialogs = find_cst_dialogs()
        # Strip hwnd for serialisation
        for d in dialogs:
            d.pop("hwnd", None)
        if dialogs:
            return {"status": "found", "count": len(dialogs), "dialogs": dialogs}
        return {"status": "ok", "message": "No CST dialogs found."}

    def start_dialog_watcher(self) -> dict:
        """Start background thread that auto-dismisses CST dialogs."""
        if CSTClient._dialog_watcher is not None and CSTClient._dialog_watcher.running:
            return {"status": "already_running"}
        CSTClient._dialog_watcher = DialogWatcher(poll_interval=0.5)
        CSTClient._dialog_watcher.start()
        return {"status": "started"}

    def stop_dialog_watcher(self) -> dict:
        """Stop the background dialog watcher and return its log."""
        if CSTClient._dialog_watcher is None or not CSTClient._dialog_watcher.running:
            return {"status": "not_running"}
        log = CSTClient._dialog_watcher.get_log()
        CSTClient._dialog_watcher.stop()
        return {"status": "stopped", "dismissed_count": len(log), "log": log}

    def get_dialog_log(self) -> dict:
        """Get log of dialogs auto-dismissed by the watcher."""
        if CSTClient._dialog_watcher is None:
            return {"status": "not_running", "log": []}
        log = CSTClient._dialog_watcher.get_log()
        return {"status": "ok", "count": len(log), "log": log}

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
            "dialog_watcher": (
                CSTClient._dialog_watcher is not None
                and CSTClient._dialog_watcher.running
            ),
        }
