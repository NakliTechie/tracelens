"""Where tracelens reads from and writes to.

Data home: --home, else $TRACELENS_HOME, else the current directory.
Inside it: archive/claude, archive/codex and tracelens.duckdb.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AGENTS = ("claude", "codex")


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def resolve(cls, home: str | None = None) -> "Paths":
        return cls(Path(home or os.environ.get("TRACELENS_HOME") or Path.cwd()).expanduser().resolve())

    @property
    def archive(self) -> Path:
        return self.home / "archive"

    @property
    def db(self) -> Path:
        return self.home / "tracelens.duckdb"


def default_sources() -> dict[str, Path]:
    """Live transcript trees per agent; overridable for tests."""
    return {
        "claude": Path(os.environ.get("TRACELENS_CLAUDE_SRC", "~/.claude/projects")).expanduser(),
        "codex": Path(os.environ.get("TRACELENS_CODEX_SRC", "~/.codex/sessions")).expanduser(),
    }
