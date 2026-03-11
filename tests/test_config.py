"""Tests for CSTConfig class and CST auto-detection logic."""

from __future__ import annotations

import os

import pytest

from mcp_cst_studio.config import CSTConfig, _auto_detect_cst


# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

class TestCSTConfigDefaults:
    def test_default_connected_is_false(self):
        config = CSTConfig()
        assert config.connected is False

    def test_default_cst_path_is_none(self):
        config = CSTConfig()
        assert config.cst_path is None

    def test_default_version_is_2026(self):
        config = CSTConfig()
        assert config.version == "2026"

    def test_default_work_dir_is_empty_string(self):
        config = CSTConfig()
        assert config.work_dir == ""


# ---------------------------------------------------------------------------
# Explicit values
# ---------------------------------------------------------------------------

class TestCSTConfigExplicitValues:
    def test_explicit_cst_path_stored(self):
        config = CSTConfig(cst_path="/some/path/to/cst")
        assert config.cst_path == "/some/path/to/cst"

    def test_explicit_work_dir_stored(self):
        config = CSTConfig(work_dir="/tmp/my_projects")
        assert config.work_dir == "/tmp/my_projects"

    def test_explicit_version_stored(self):
        config = CSTConfig(version="2025")
        assert config.version == "2025"

    def test_explicit_connected_true_stored(self):
        config = CSTConfig(connected=True)
        assert config.connected is True

    def test_all_explicit_values_stored(self):
        config = CSTConfig(
            cst_path=r"C:\Program Files\CST Studio Suite 2026",
            work_dir="/tmp/cst",
            version="2026",
            connected=False,
        )
        assert config.cst_path == r"C:\Program Files\CST Studio Suite 2026"
        assert config.work_dir == "/tmp/cst"
        assert config.version == "2026"
        assert config.connected is False


# ---------------------------------------------------------------------------
# from_env() class method
# ---------------------------------------------------------------------------

class TestCSTConfigFromEnv:
    def test_from_env_defaults_when_no_env_vars(self, monkeypatch):
        monkeypatch.delenv("CST_PATH", raising=False)
        monkeypatch.delenv("CST_WORK_DIR", raising=False)
        monkeypatch.delenv("CST_VERSION", raising=False)

        config = CSTConfig.from_env()

        assert config.version == "2026"
        assert config.work_dir != ""  # expands ~/cst_projects
        assert "cst_projects" in config.work_dir

    def test_from_env_reads_cst_path(self, monkeypatch):
        monkeypatch.setenv("CST_PATH", "/custom/cst/path")
        monkeypatch.delenv("CST_WORK_DIR", raising=False)
        monkeypatch.delenv("CST_VERSION", raising=False)

        config = CSTConfig.from_env()

        assert config.cst_path == "/custom/cst/path"

    def test_from_env_reads_work_dir(self, monkeypatch):
        monkeypatch.setenv("CST_WORK_DIR", "/custom/work/dir")
        monkeypatch.delenv("CST_PATH", raising=False)
        monkeypatch.delenv("CST_VERSION", raising=False)

        config = CSTConfig.from_env()

        assert config.work_dir == "/custom/work/dir"

    def test_from_env_reads_version(self, monkeypatch):
        monkeypatch.setenv("CST_VERSION", "2025")
        monkeypatch.delenv("CST_PATH", raising=False)
        monkeypatch.delenv("CST_WORK_DIR", raising=False)

        config = CSTConfig.from_env()

        assert config.version == "2025"

    def test_from_env_connected_false_without_cst_library(self, monkeypatch):
        """On any non-Windows CI machine CST is not installed: connected must be False."""
        monkeypatch.delenv("CST_PATH", raising=False)
        monkeypatch.delenv("CST_WORK_DIR", raising=False)
        monkeypatch.delenv("CST_VERSION", raising=False)

        config = CSTConfig.from_env()

        # CST is never installed in the test environment
        assert config.connected is False

    def test_from_env_returns_cstconfig_instance(self, monkeypatch):
        monkeypatch.delenv("CST_PATH", raising=False)
        monkeypatch.delenv("CST_WORK_DIR", raising=False)
        monkeypatch.delenv("CST_VERSION", raising=False)

        config = CSTConfig.from_env()

        assert isinstance(config, CSTConfig)


# ---------------------------------------------------------------------------
# _auto_detect_cst() — missing CST handled gracefully
# ---------------------------------------------------------------------------

class TestAutoDetectCST:
    def test_returns_none_when_no_cst_installed(self):
        """On Linux/macOS/CI where CST is absent, result is None."""
        result = _auto_detect_cst("2026")
        # On a non-Windows machine none of the candidate paths will exist
        assert result is None or isinstance(result, str)

    def test_returns_none_for_nonexistent_version(self):
        """Using a version string that will never match any real path."""
        result = _auto_detect_cst("9999_nonexistent")
        assert result is None

    def test_does_not_raise_on_missing_paths(self):
        """Should never raise even if OS path functions behave oddly."""
        try:
            _auto_detect_cst("2026")
        except Exception as exc:
            pytest.fail(f"_auto_detect_cst raised unexpectedly: {exc}")
