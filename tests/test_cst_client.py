"""Tests for CSTClient -- execute_vba, execute_vba_silent, DialogWatcher integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcp_cst_studio.cst_client import CSTClient
from mcp_cst_studio.config import CSTConfig


@pytest.fixture
def offline_client() -> CSTClient:
    return CSTClient(config=CSTConfig(connected=False))


@pytest.fixture
def mock_client() -> CSTClient:
    """CSTClient with mocked CST project for testing connected-mode paths."""
    config = CSTConfig(
        cst_path=r"C:\Program Files\CST Studio Suite 2026",
        work_dir="/tmp/cst_test",
        version="2026",
        connected=True,
    )
    client = CSTClient(config=config)
    client._project = MagicMock()
    client._project_path = r"C:\test\project.cst"
    # Patch the connected property to bypass CST_AVAILABLE check
    type(client).connected = property(lambda self: True)
    return client


class TestExecuteVbaOffline:
    def test_returns_offline_status(self, offline_client: CSTClient):
        result = offline_client.execute_vba('MsgBox "Hello"')
        assert result["status"] == "offline"
        assert "vba" in result
        assert 'MsgBox "Hello"' in result["vba"]

    def test_returns_vba_script(self, offline_client: CSTClient):
        vba = 'Sub Main()\nSolver.Start\nEnd Sub'
        result = offline_client.execute_vba(vba)
        assert result["vba"] == vba


class TestExecuteVbaSilentOffline:
    def test_returns_offline_status(self, offline_client: CSTClient):
        vba = 'Sub Main()\nDim x As Double\nEnd Sub'
        result = offline_client.execute_vba_silent(vba)
        assert result["status"] == "offline"
        assert "vba" in result

    def test_returns_silent_message(self, offline_client: CSTClient):
        result = offline_client.execute_vba_silent("Sub Main()\nEnd Sub")
        assert "silent" in result.get("message", "").lower()


class TestExecuteVbaConnected:
    def test_calls_add_to_history(self, mock_client: CSTClient):
        """Connected execute_vba should call model3d.add_to_history."""
        mock_client._project.model3d.add_to_history.return_value = None

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            result = mock_client.execute_vba('Brick.Reset')
            assert result["status"] == "executed"
            mock_client._project.model3d.add_to_history.assert_called_once()

    def test_starts_and_stops_dialog_watcher(self, mock_client: CSTClient):
        """DialogWatcher should start before and stop after VBA execution."""
        mock_client._project.model3d.add_to_history.return_value = None

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            mock_client.execute_vba('Brick.Reset')
            watcher_instance.start.assert_called_once()
            watcher_instance.stop.assert_called_once()

    def test_dialog_watcher_stops_on_exception(self, mock_client: CSTClient):
        """DialogWatcher must stop even if add_to_history raises."""
        mock_client._project.model3d.add_to_history.side_effect = RuntimeError("CST error")

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            result = mock_client.execute_vba('Brick.Reset')
            assert result["status"] == "error"
            watcher_instance.stop.assert_called_once()

    def test_reports_dismissed_dialogs(self, mock_client: CSTClient):
        """If dialogs were dismissed, result should include count and log."""
        mock_client._project.model3d.add_to_history.return_value = None

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = [
                {"title": "Results May Get Incompatible", "action": "clicked_ok"},
            ]
            MockWatcher.return_value = watcher_instance

            result = mock_client.execute_vba('Brick.Reset')
            assert result["status"] == "executed"
            assert result["dialogs_dismissed"] == 1
            assert len(result["dialog_log"]) == 1

    def test_custom_history_label(self, mock_client: CSTClient):
        """execute_vba should use custom history label if provided."""
        mock_client._project.model3d.add_to_history.return_value = None

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            mock_client.execute_vba('Brick.Reset', history_label="Create Patch")
            call_args = mock_client._project.model3d.add_to_history.call_args
            assert call_args[0][0] == "Create Patch"


class TestExecuteVbaSilentConnected:
    def test_calls_schematic_execute(self, mock_client: CSTClient):
        """Connected execute_vba_silent should call schematic.execute_vba_code."""
        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            result = mock_client.execute_vba_silent("Sub Main()\nEnd Sub")
            assert result["status"] == "executed"
            mock_client._project.schematic.execute_vba_code.assert_called_once()

    def test_starts_and_stops_dialog_watcher(self, mock_client: CSTClient):
        """DialogWatcher should wrap schematic.execute_vba_code."""
        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            mock_client.execute_vba_silent("Sub Main()\nEnd Sub")
            watcher_instance.start.assert_called_once()
            watcher_instance.stop.assert_called_once()

    def test_dialog_watcher_stops_on_exception(self, mock_client: CSTClient):
        """DialogWatcher must stop even if execute_vba_code raises."""
        mock_client._project.schematic.execute_vba_code.side_effect = RuntimeError("err")

        with patch("mcp_cst_studio.cst_client.DialogWatcher") as MockWatcher:
            watcher_instance = MagicMock()
            watcher_instance.get_log.return_value = []
            MockWatcher.return_value = watcher_instance

            result = mock_client.execute_vba_silent("Sub Main()\nEnd Sub")
            assert result["status"] == "error"
            watcher_instance.stop.assert_called_once()
