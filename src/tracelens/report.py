"""Counts by project / agent / week, plus the top sessions per signal.

Output is bounded: it grows with the number of weeks and the --top limit,
never with the archive size.
"""

from __future__ import annotations

import duckdb

from .signals import SIGNALS

SIG_COLS = ", ".join(f"count(*) FILTER (WHERE g.signal = '{s}') AS {s}" for s in SIGNALS)


def build_report(con: duckdb.DuckDBPyConnection, top: int = 5, top_projects: int = 15) -> dict:
    def rows(sql: str, params=None) -> list[dict]:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    totals = rows(f"""
        SELECT s.agent, count(DISTINCT s.file) AS sessions,
               count(DISTINCT s.file) FILTER (WHERE s.kind = 'main') AS main_sessions,
               sum(s.n_prompts) AS prompts, sum(s.n_tool_calls) AS tool_calls,
               sum(s.n_tool_errors) AS tool_errors,
               min(s.started_at)::DATE AS first_day, max(s.ended_at)::DATE AS last_day
        FROM sessions s GROUP BY 1 ORDER BY 1""")
    sig = f"""
        WITH sess AS (SELECT {{key}}, count(*) AS sessions, sum(n_tool_calls) AS tool_calls
                      FROM sessions s GROUP BY ALL),
        sg AS (SELECT {{gkey}}, {SIG_COLS} FROM signals g JOIN sessions s USING (file) GROUP BY ALL)
        SELECT * FROM sess LEFT JOIN sg USING ({{using}})"""
    by_agent_week = rows(sig.format(
        key="agent, date_trunc('week', started_at)::DATE AS week",
        gkey="s.agent, date_trunc('week', s.started_at)::DATE AS week", using="agent, week")
        + " ORDER BY week, agent")
    by_project = rows(sig.format(key="project", gkey="s.project", using="project")
                      + f" ORDER BY {' + '.join('coalesce(' + s + ', 0)' for s in SIGNALS)} DESC, sessions DESC"
                      + f" LIMIT {int(top_projects)}")
    top_sessions = {}
    for s in SIGNALS:
        top_sessions[s] = rows("""
            SELECT g.agent, g.project, g.session_id, g.file, count(*) AS hits,
                   min(g.line) AS first_line, arg_min(g.detail, g.line) AS example
            FROM signals g WHERE g.signal = ? GROUP BY ALL ORDER BY hits DESC, g.file LIMIT ?""", [s, top])
    signal_totals = dict(con.execute("SELECT signal, count(*) FROM signals GROUP BY 1").fetchall())
    return {"totals": totals, "signals": {s: signal_totals.get(s, 0) for s in SIGNALS},
            "by_agent_week": by_agent_week, "by_project": by_project, "top_sessions": top_sessions}


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "  (none)"
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    w = [max(len(c), *(len(x[i]) for x in cells)) for i, c in enumerate(cols)]
    line = lambda xs: "  " + "  ".join(x.ljust(w[i]) for i, x in enumerate(xs))
    return "\n".join([line(cols), line(["-" * n for n in w])] + [line(x) for x in cells])


ABBR = {"repeated_failure": "rep_fail", "approval_stall": "stall", "edit_without_read": "edit_nr",
        "unverified_claim": "unverif", "user_correction": "correct"}


def render(rep: dict) -> str:
    out = ["# tracelens report", "", "## Totals"]
    out.append(_table(rep["totals"], ["agent", "sessions", "main_sessions", "prompts", "tool_calls",
                                      "tool_errors", "first_day", "last_day"]))
    out += ["", "## Signals", _table([{"signal": k, "rows": v} for k, v in rep["signals"].items()], ["signal", "rows"])]
    short = lambda rs: [{**r, **{ABBR[s]: r.get(s) or 0 for s in SIGNALS}} for r in rs]
    out += ["", "## By agent and week (week starts Monday, IST)",
            _table([{**r, "week": r["week"] or "(no ts)"} for r in short(rep["by_agent_week"])],["week", "agent", "sessions", "tool_calls", *ABBR.values()])]
    out += ["", "## By project (top by signal rows)",
            _table(short(rep["by_project"]), ["project", "sessions", "tool_calls", *ABBR.values()])]
    for s, rs in rep["top_sessions"].items():
        rs = [{**r, "cite": f"{r['file']}:{r['first_line']}", "example": (r["example"] or "")[:90]} for r in rs]
        out += ["", f"## Top sessions: {s}", _table(rs, ["hits", "agent", "project", "cite", "example"])]
    return "\n".join(out)
