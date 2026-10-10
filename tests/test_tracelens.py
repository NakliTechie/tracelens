"""End-to-end tests over the synthetic fixture archive (tests/fixtures/build.py)."""

import json
import os
import shutil
from pathlib import Path

import duckdb
import pytest

from tracelens.archive import archive_tree
from tracelens.cli import main
from tracelens.index import build_index
from tracelens.signals import run_signals

FIX = Path(__file__).parent / "fixtures" / "archive"
CLAUDE = "claude/-tmp-fx-alpha/11111111-1111-4111-8111-111111111111.jsonl"
CLAUDE_SUB = "claude/-tmp-fx-alpha/11111111-1111-4111-8111-111111111111/subagents/agent-a1.jsonl"
CODEX = "codex/2026/01/05/rollout-2026-01-05T10-00-00-22222222-2222-4222-8222-222222222222.jsonl"


@pytest.fixture(scope="module")
def con(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "t.duckdb"
    build_index(FIX, db)
    c = duckdb.connect(str(db))
    run_signals(c)
    yield c
    c.close()


def one(con, sql, *p):
    return con.execute(sql, list(p)).fetchone()[0]


def test_row_counts(con):
    assert one(con, "SELECT count(*) FROM sessions") == 3
    assert one(con, "SELECT count(*) FROM turns WHERE file = ?", CLAUDE) == 29
    assert one(con, "SELECT count(*) FROM turns WHERE file = ?", CLAUDE_SUB) == 2
    assert one(con, "SELECT count(*) FROM turns WHERE file = ?", CODEX) == 5
    assert one(con, "SELECT count(*) FROM tool_calls WHERE file = ?", CLAUDE) == 9
    assert one(con, "SELECT count(*) FROM tool_calls WHERE file = ?", CODEX) == 6
    assert one(con, "SELECT count(*) FROM errors WHERE file = ?", CLAUDE) == 5
    assert one(con, "SELECT count(*) FROM errors WHERE file = ?", CODEX) == 3
    assert one(con, "SELECT n_prompts FROM sessions WHERE file = ?", CLAUDE) == 4
    assert one(con, "SELECT n_prompts FROM sessions WHERE file = ?", CODEX) == 2


def test_session_fields(con):
    row = con.execute("SELECT session_id, kind, project, model, cost_usd FROM sessions WHERE file = ?", [CLAUDE]).fetchone()
    assert row == ("11111111-1111-4111-8111-111111111111", "main", "alpha", "claude-test-1", 0.42)
    row = con.execute("SELECT session_id, parent_session_id, kind, project FROM sessions WHERE file = ?", [CLAUDE_SUB]).fetchone()
    assert row == ("a1", "11111111-1111-4111-8111-111111111111", "subagent", "alpha")
    row = con.execute("SELECT session_id, kind, project, model, originator FROM sessions WHERE file = ?", [CODEX]).fetchone()
    assert row == ("22222222-2222-4222-8222-222222222222", "main", "beta", "gpt-test", "Codex Desktop")


def test_tool_call_fields(con):
    assert con.execute("SELECT is_error, exit_code, wait_s FROM tool_calls WHERE call_id = 't4'").fetchone() == (False, 0, 25 * 60)
    assert con.execute("SELECT exit_code, result_line FROM tool_calls WHERE call_id = 't1'").fetchone() == (1, 3)
    assert con.execute("SELECT is_error, exit_code, duration_s FROM tool_calls WHERE call_id = 'c2'").fetchone() == (True, 2, 1.5)
    assert one(con, "SELECT count(*) FROM file_reads WHERE path = '/tmp/fx/beta/config.toml'") == 1


EXPECTED = {
    ("repeated_failure", CLAUDE, 4), ("repeated_failure", CODEX, 9),
    ("approval_stall", CLAUDE, 8),
    ("edit_without_read", CLAUDE, 14), ("edit_without_read", CODEX, 6),
    ("unverified_claim", CLAUDE, 21), ("unverified_claim", CODEX, 11),
    ("user_correction", CLAUDE, 22), ("user_correction", CLAUDE, 27), ("user_correction", CODEX, 12),
}


def test_signals_exact(con):
    got = set(con.execute("SELECT signal, file, line FROM signals").fetchall())
    assert got == EXPECTED, f"missing {EXPECTED - got}, extra {got - EXPECTED}"


def test_archive_incremental(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIX / "claude", src)
    dst = tmp_path / "archive" / "claude"
    first = archive_tree("claude", src, dst)
    assert (first.scanned, first.copied) == (2, 2)
    second = archive_tree("claude", src, dst)
    assert (second.scanned, second.copied) == (2, 0)
    f = next(src.rglob("agent-a1.jsonl"))
    f.write_text(f.read_text() + "\n")
    third = archive_tree("claude", src, dst)
    assert third.copied == 1
    f.unlink()  # source cleanup must not remove the archived copy
    assert archive_tree("claude", src, dst).copied == 0
    assert (dst / f.relative_to(src)).exists()


def test_cli_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRACELENS_CLAUDE_SRC", str(FIX / "claude"))
    monkeypatch.setenv("TRACELENS_CODEX_SRC", str(FIX / "codex"))
    home = ["--home", str(tmp_path)]
    assert main(["report", *home]) == 3  # no index yet: names the remedy
    assert "tracelens index" in capsys.readouterr().out
    assert main(["archive", *home]) == 0
    assert main(["index", *home, "--json"]) == 0
    capsys.readouterr()
    assert main(["report", *home, "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert sum(rep["signals"].values()) == len(EXPECTED)
    assert main(["status", *home, "--json"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["archive"]["codex"]["files"] == 1 and st["next"] is None
