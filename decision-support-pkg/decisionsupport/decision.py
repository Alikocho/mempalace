"""
Framework toggle for the decision support tool.

Persists the active framework (cynefin or delphi) to
~/.decisionsupport/decision_framework so the choice survives between sessions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple

FRAMEWORKS: Tuple[str, ...] = ("cynefin", "delphi", "premortem", "matrix", "sixhats", "swot", "pestle")
_DEFAULT = "cynefin"
_CONFIG_PATH = Path.home() / ".decisionsupport" / "decision_framework"


def get_framework(config_path: Path = _CONFIG_PATH) -> str:
    """Return the currently active framework name."""
    if config_path.exists():
        value = config_path.read_text(encoding="utf-8").strip().lower()
        if value in FRAMEWORKS:
            return value
    return _DEFAULT


def set_framework(framework: str, config_path: Path = _CONFIG_PATH) -> None:
    """Persist a framework choice. Raises ValueError for unknown names."""
    if framework not in FRAMEWORKS:
        raise ValueError(f"Unknown framework {framework!r}. Choose from: {', '.join(FRAMEWORKS)}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(framework, encoding="utf-8")


def toggle(config_path: Path = _CONFIG_PATH) -> str:
    """Flip to the other framework and return its name."""
    current = get_framework(config_path)
    others = [f for f in FRAMEWORKS if f != current]
    new = others[0]
    set_framework(new, config_path)
    return new
