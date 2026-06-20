"""
test_decision.py — Tests for the decision framework toggle.
"""
from __future__ import annotations

import pytest

from mempalace.decision import FRAMEWORKS, get_framework, set_framework, toggle


@pytest.fixture
def cfg(tmp_path):
    """Isolated config file path for each test."""
    return tmp_path / "decision_framework"


class TestGetFramework:
    def test_defaults_to_cynefin(self, cfg):
        assert get_framework(cfg) == "cynefin"

    def test_reads_persisted_value(self, cfg):
        cfg.write_text("delphi")
        assert get_framework(cfg) == "delphi"

    def test_ignores_unknown_value(self, cfg):
        cfg.write_text("unknown_framework")
        assert get_framework(cfg) == "cynefin"  # falls back to default

    def test_ignores_empty_file(self, cfg):
        cfg.write_text("")
        assert get_framework(cfg) == "cynefin"


class TestSetFramework:
    def test_persists_cynefin(self, cfg):
        set_framework("cynefin", cfg)
        assert cfg.read_text() == "cynefin"

    def test_persists_delphi(self, cfg):
        set_framework("delphi", cfg)
        assert cfg.read_text() == "delphi"
        assert get_framework(cfg) == "delphi"

    def test_unknown_framework_raises(self, cfg):
        with pytest.raises(ValueError, match="Unknown framework"):
            set_framework("scrum", cfg)

    def test_creates_parent_dirs(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "fw"
        set_framework("cynefin", deep)
        assert deep.read_text() == "cynefin"


class TestToggle:
    def test_toggle_cynefin_to_delphi(self, cfg):
        set_framework("cynefin", cfg)
        result = toggle(cfg)
        assert result == "delphi"
        assert get_framework(cfg) == "delphi"

    def test_toggle_delphi_to_cynefin(self, cfg):
        set_framework("delphi", cfg)
        result = toggle(cfg)
        assert result == "cynefin"
        assert get_framework(cfg) == "cynefin"

    def test_double_toggle_returns_to_start(self, cfg):
        set_framework("cynefin", cfg)
        toggle(cfg)
        toggle(cfg)
        assert get_framework(cfg) == "cynefin"

    def test_toggle_returns_new_framework_name(self, cfg):
        set_framework("cynefin", cfg)
        new = toggle(cfg)
        assert new in FRAMEWORKS
        assert new != "cynefin"

    def test_all_frameworks_reachable(self, cfg):
        """Every framework should be reachable by toggling."""
        seen = set()
        fw = get_framework(cfg)
        seen.add(fw)
        for _ in range(len(FRAMEWORKS) + 1):
            fw = toggle(cfg)
            seen.add(fw)
        assert seen >= set(FRAMEWORKS)


class TestDecisionCLI:
    def _run(self, argv, cfg_path, capsys):
        import sys
        from unittest.mock import patch


        with patch.object(
            sys, "argv", ["decision"] + argv
        ), patch("mempalace.decision._CONFIG_PATH", cfg_path), patch(
            "mempalace.decision_cli.get_framework",
            lambda config_path=None: get_framework(cfg_path),
        ), patch(
            "mempalace.decision_cli.set_framework",
            lambda fw, config_path=None: set_framework(fw, cfg_path),
        ), patch(
            "mempalace.decision_cli.toggle",
            lambda config_path=None: toggle(cfg_path),
        ):
            from mempalace.decision_cli import main

            main()
        return capsys.readouterr().out

    def test_toggle_command(self, cfg, capsys):
        set_framework("cynefin", cfg)
        out = self._run(["toggle"], cfg, capsys)
        assert "delphi" in out.lower() or "Switched" in out
        assert get_framework(cfg) == "delphi"

    def test_framework_show(self, cfg, capsys):
        set_framework("cynefin", cfg)
        out = self._run(["framework"], cfg, capsys)
        assert "cynefin" in out.lower()

    def test_framework_set(self, cfg, capsys):
        set_framework("cynefin", cfg)
        self._run(["framework", "delphi"], cfg, capsys)
        assert get_framework(cfg) == "delphi"

    def test_no_args_prints_status(self, cfg, capsys):
        out = self._run([], cfg, capsys)
        assert "decision" in out.lower() or "framework" in out.lower()
