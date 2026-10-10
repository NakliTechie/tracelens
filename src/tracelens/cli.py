"""tracelens CLI. Every command takes --json; exit codes are in MANIFEST."""

from __future__ import annotations

import argparse
import json
import sys
import time

import duckdb

from . import __version__
from .archive import archive_tree
from .index import build_index
from .paths import AGENTS, Paths, default_sources
from .report import build_report, render
from .signals import run_signals

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE, EXIT_MISSING = 0, 1, 2, 3

# The agent face: every command, its effect, its outputs and its exit codes.
MANIFEST = {
    "name": "tracelens",
    "version": __version__,
    "data_home": "--home, else $TRACELENS_HOME, else the current directory",
    "exit_codes": {
        "0": "ok",
        "1": "partial: some source files could not be copied; the rest landed (see errors)",
        "2": "usage error",
        "3": "missing prerequisite: run the command named in `remedy`",
    },
    "commands": {
        "status": {"effect": "read-only", "does": "one-read picture: archive size, index age, row and signal counts"},
        "archive": {"effect": "writes archive/", "does": "incremental copy of ~/.claude/projects and ~/.codex/sessions JSONL; never touches the sources"},
        "index": {"effect": "rewrites tracelens.duckdb atomically", "does": "parse archive/ into sessions, turns, tool_calls, file_reads, errors", "needs": "archive"},
        "signals": {"effect": "rewrites the signals table", "does": "run the five rule signals", "needs": "index"},
        "report": {"effect": "read-only (runs signals first if the table is missing)", "does": "counts by project / agent / week and top sessions per signal", "needs": "index", "args": {"--top": "sessions per signal, default 5"}},
        "manifest": {"effect": "read-only", "does": "print this manifest"},
    },
    "person_only": ["deleting archive/ or tracelens.duckdb", "sending any transcript content off this machine"],
}


def _emit(args, payload: dict, text: str) -> None:
    print(json.dumps(payload, default=str, indent=1) if args.json else text)


def _missing(args, what: str, remedy: str) -> int:
    _emit(args, {"error": "missing", "what": what, "remedy": remedy}, f"error: {what}\nremedy: {remedy}")
    return EXIT_MISSING


def _connect(p: Paths, read_only: bool):
    return duckdb.connect(str(p.db), read_only=read_only, config={"TimeZone": "Asia/Kolkata"})


def cmd_archive(args, p: Paths) -> int:
    results = [archive_tree(a, src, p.archive / a) for a, src in default_sources().items()]
    payload = {r.agent: {"scanned": r.scanned, "copied": r.copied, "bytes_copied": r.bytes_copied,
                         "errors": r.errors[:20], "n_errors": len(r.errors)} for r in results}
    text = "\n".join(f"{r.agent}: scanned {r.scanned}, copied {r.copied} ({r.bytes_copied / 1e6:.1f} MB), "
                     f"errors {len(r.errors)}" for r in results)
    _emit(args, payload, text)
    return EXIT_PARTIAL if any(r.errors for r in results) else EXIT_OK


def cmd_index(args, p: Paths) -> int:
    if not any((p.archive / a).is_dir() for a in AGENTS):
        return _missing(args, f"no archive at {p.archive}", "tracelens archive")
    log = (lambda m: print(m, file=sys.stderr)) if not args.json else None
    stats = build_index(p.archive, p.db, progress=log)
    _emit(args, stats, "\n".join(f"{k}: {v}" for k, v in stats.items()))
    return EXIT_OK


def cmd_signals(args, p: Paths) -> int:
    if not p.db.exists():
        return _missing(args, f"no index at {p.db}", "tracelens index")
    t0 = time.time()
    with _connect(p, read_only=False) as con:
        counts = run_signals(con)
    counts_s = {**counts, "seconds": round(time.time() - t0, 1)}
    _emit(args, counts_s, "\n".join(f"{k}: {v}" for k, v in counts_s.items()))
    return EXIT_OK


def cmd_report(args, p: Paths) -> int:
    if not p.db.exists():
        return _missing(args, f"no index at {p.db}", "tracelens index")
    t0 = time.time()
    with _connect(p, read_only=False) as con:
        has = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'signals'").fetchone()[0]
        if not has:
            run_signals(con)
        rep = build_report(con, top=args.top)
    rep["seconds"] = round(time.time() - t0, 1)
    _emit(args, rep, render(rep) + f"\n\n(report took {rep['seconds']} s)")
    return EXIT_OK


def cmd_status(args, p: Paths) -> int:
    st = {"home": str(p.home), "archive": {}, "index": None}
    for a in AGENTS:
        files = list((p.archive / a).rglob("*.jsonl")) if (p.archive / a).is_dir() else []
        st["archive"][a] = {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
    remedy = None
    if p.db.exists():
        with _connect(p, read_only=True) as con:
            st["index"] = dict(con.execute("SELECT key, value FROM index_meta").fetchall())
            has = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'signals'").fetchone()[0]
            st["signals"] = dict(con.execute("SELECT signal, count(*) FROM signals GROUP BY 1").fetchall()) if has else None
        if st["signals"] is None:
            remedy = "tracelens signals"
    else:
        remedy = "tracelens index" if any(v["files"] for v in st["archive"].values()) else "tracelens archive"
    st["next"] = remedy
    lines = [f"home: {p.home}"] + [f"archive/{a}: {v['files']} files, {v['bytes'] / 1e9:.2f} GB" for a, v in st["archive"].items()]
    if st["index"]:
        ix = st["index"]
        lines.append(f"index: built {ix.get('built_at')}, {ix.get('sessions')} sessions, {ix.get('turns')} turns, "
                     f"{ix.get('tool_calls')} tool calls, {ix.get('errors')} errors")
    if st.get("signals"):
        lines.append("signals: " + ", ".join(f"{k} {v}" for k, v in sorted(st["signals"].items())))
    if remedy:
        lines.append(f"next: {remedy}")
    _emit(args, st, "\n".join(lines))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tracelens", description="Local trace investigator for Claude Code and Codex sessions.")
    ap.add_argument("--version", action="version", version=f"tracelens {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--home", help="data directory holding archive/ and tracelens.duckdb")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "archive", "index", "signals", "report", "manifest"):
        sp = sub.add_parser(name, parents=[common], help=MANIFEST["commands"][name]["does"])
        if name == "report":
            sp.add_argument("--top", type=int, default=5)
    args = ap.parse_args(argv)
    if args.cmd == "manifest":
        print(json.dumps(MANIFEST, indent=1))
        return EXIT_OK
    p = Paths.resolve(args.home)
    return {"status": cmd_status, "archive": cmd_archive, "index": cmd_index,
            "signals": cmd_signals, "report": cmd_report}[args.cmd](args, p)


if __name__ == "__main__":
    sys.exit(main())
