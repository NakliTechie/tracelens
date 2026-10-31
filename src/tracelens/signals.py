"""Ten rule signals over the index. No model. Each row cites file + line.

The rules, their exact definitions and their known blind spots are in
docs/signals.md. Every rule is one SQL statement inserting into `signals`.
"""

from __future__ import annotations

import duckdb

SIGNALS = ("repeated_failure", "approval_stall", "edit_without_read", "unverified_claim", "user_correction",
           "recurring_failure", "recurring_action", "no_progress_streak", "repeated_args", "loop_cost_outlier")

STALL_S = 20 * 60

# Tools that legitimately block longer than 20 minutes: subagents, waits,
# questions to the user, plan review. A foreground Bash call cannot run past
# its 10-minute timeout cap, and a backgrounded one returns its task id at once,
# so Bash stays in scope either way.
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch")
# Commands whose exit 1 means "different" or "no match", not a failure.
DIFF_LIKE = r"^\s*(git\s+diff|diff|grep|rg|cmp|test|\[)(\s|$)"

LONG_TOOLS = ("Agent", "Task", "TaskOutput", "Monitor", "Workflow", "AskUserQuestion",
              "ExitPlanMode", "ScheduleWakeup", "SendMessage", "Skill")

# ATTEST-100 §3 reserved words.
RESERVED = (r"(?i)\b(done|complete|completed|fixed|resolved|passing|passes|green|works|working|correct|"
            r"safe|secure|verified|confirmed|proven|no regressions|production-ready)\b")
# What counts as a verifier pointer: an exit code, a pass count, a path:line,
# an explicit evidence line, a backticked command with its result, a link to a
# local file (an artifact path), a test/suite count, "N of M", "by `cmd`", a
# backticked commit hash, a commit after "@" or "commit", a URL, a file path.
_POINTERS = [
    r"exit(\s+code)?\s*[:=]?\s*\d",                      # exit code 0
    r"\b\d+\s*/\s*\d+\b", r"\b\d+ of \d+\b",              # 12/12, 50 of 50
    r"\b\d+\s+(tests?\s+)?(passed|failed|passing)\b",       # 8 passed
    r"\b\d+\s+(tests?|suites?|checks?|specs?)\b",            # 733 tests
    r"[\w./-]+\.\w+:\d+",                                  # path:line
    r"evidence:",
    r"`[^`]+`\s*(passes|passed|green|clean|exits?|returns?|->|→)",  # `cmd` passes
    r"\]\((/|~|\.{1,2}/|[\w-]+/)[^)\s]+\)",                # [x](local/path)
    r"\b(by|via|per) `[^`]+`",                               # by `df`
    r"`[0-9a-f]{7,40}`", r"(@\s*|\bcommit\s+)[0-9a-f]{7,40}\b",  # commit hashes
    r"https?://\S+",                                         # a URL
    r"\b[\w.-]+/[\w./-]*\.[a-z]{1,6}\b",                     # a file path
]
POINTER = "(?i)(" + "|".join(_POINTERS) + ")"
# Phrases that use a reserved word without claiming success.
NOT_A_CLAIM = (r"(?i)\b(working (tree|directory|dir|copy)|safe to|is it (done|working)|not (yet )?(done|verified|fixed|working)|"
               r"when (it'?s|it is|that'?s|you'?re) done|want (that|it) done|how it works|green (\*\*)?[\w ]{0,20}button|"
               r"done/stop|done or paused|to complete|needs? to be (done|fixed|verified))\b")
# Headless permission probes (`claude -p` harnesses) are not sessions to judge.
PROBE_PROMPT = r"^(Call the Bash tool exactly once|Use the Edit tool exactly once)"

# A leading "no" or a bare "stop." pushes back; "no need", "no luck", "no, that's fine"
# and "stop ollama" do not. Explicit markers count anywhere in the turn.
CORRECTION_START = r"(?i)^\W*(no|nope|wrong)\b|^\W*stop(\W*$|[.!,]|\s+(it|that|doing|now)\b)"
NOT_CORRECTION = (r"(?i)^\W*no\W+(luck|worries|problem|rush|thanks|new|need|point)\b|"
                  r"^\W*no\W+(it'?s|that'?s|this is|both are|all) (fine|good|ok)\b|"
                  r"^\W*no\W+\w+(\s+\w+)?\s+(needed|required)\b")
CORRECTION_ANY = (r"(?i)(\bthat'?s (wrong|not right|incorrect|not what)|\bthis is wrong\b|"
                  r"\bnot what i (asked|meant|wanted)\b|\byou'?re wrong\b|\byou are wrong\b|\bi (meant|said)\b)")

# Cross-session and loop signals (6-10). An error or command "shape" masks
# paths, long hex runs and numbers, so the same failure on another file or
# line still matches. The streak keeps paths: a check on another file is
# progress, not a repeat.
PATH_RE = r"""(/[^\s:'"]+)+"""
HEX_RE = r"[0-9a-f]{7,}"
NUM_RE = r"[0-9]+"
REJECTED = r"(?i)doesn't want to proceed|user rejected|tool use was rejected"
ACTION_MIN = 60      # masked command length: shorter ones are everyday commands
ACTION_SESSIONS = 3  # sessions of one project before a command is "recurring"
STREAK_MIN = 4       # identical check results in a row
STREAK_FILES = 3     # at most this many files edited during the streak
ARGS_MIN = 200       # characters of input for a repeated call to count
ARGS_REPEAT = 5      # identical successful calls in one session
OUTLIER_X = 3        # turns above this multiple of the project's median
OUTLIER_FLOOR = 50   # the median is over main sessions this long; scheduled runs and probes are shorter
OUTLIER_SESSIONS = 3 # sessions that long a project needs before it has a median
OUTLIER_LOOPS = 3    # loop rows the session needs: the loops, not the work, explain its length


def lit(s: str) -> str:
    """SQL string literal; DuckDB does not process backslash escapes."""
    return "'" + s.replace("'", "''") + "'"


def shape(x: str, n: int, keep_paths: bool = False) -> str:
    """SQL for the masked, lower-cased first n characters of column x."""
    e = f"lower(left(coalesce({x}, ''), {n}))"
    if not keep_paths:
        e = f"regexp_replace({e}, {lit(PATH_RE)}, '<p>', 'g')"
    e = f"regexp_replace({e}, {lit(HEX_RE)}, '<h>', 'g')"
    return f"regexp_replace({e}, {lit(NUM_RE)}, '<n>', 'g')"


SQL = f"""
CREATE OR REPLACE TABLE signals (
  signal VARCHAR, agent VARCHAR, session_id VARCHAR, project VARCHAR, file VARCHAR,
  line INTEGER, ts TIMESTAMPTZ, detail VARCHAR);

-- 1. Same tool, same input, failing again in the same session with no edit in
--    between (a test-fix-rerun loop is not a blind retry). Exit 130 (interrupted)
--    and exit 1 from diff/grep-style commands (meaning "different" or "no match")
--    are not failures here. One row per (tool, input) group, cited at the first
--    blind repeat.
INSERT INTO signals
SELECT 'repeated_failure', r.agent, s.session_id, s.project, r.file, min(r.line), min(r.ts),
       any_value(r.tool) || ' failed ' || (count(*) + 1) || 'x with identical input and no edit between: '
       || left(any_value(coalesce(r.command, r.input, '')), 160)
FROM (SELECT f.* FROM (
        SELECT *, md5(coalesce(input, command, '')) AS h,
               lag(line) OVER (PARTITION BY file, tool, md5(coalesce(input, command, '')) ORDER BY line) AS prev_line
        FROM tool_calls
        WHERE is_error AND coalesce(exit_code, 0) <> 130
          AND NOT (exit_code = 1 AND regexp_matches(coalesce(command, ''), {lit(DIFF_LIKE)}))) f
      WHERE f.prev_line IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM tool_calls e WHERE e.file = f.file AND e.line > f.prev_line
                        AND e.line < f.line AND e.tool IN {EDIT_TOOLS})) r
JOIN sessions s USING (file)
GROUP BY r.agent, s.session_id, s.project, r.file, r.tool, r.h;

-- 2. A call whose result arrived > 20 min after the call, from a tool that cannot
--    legitimately run that long: the session sat on a permission prompt. Calls
--    issued in parallel get their results back together once one is approved
--    (results within 10 s of each other), so they form one stall, cited at the
--    first call of the group.
INSERT INTO signals
SELECT 'approval_stall', 'claude', any_value(s.session_id), any_value(s.project), g.file,
       min(g.line), min(g.ts),
       string_agg(g.tool, ' + ' ORDER BY g.line) || ' waited ' || round(max(g.wait_s) / 60)
         || ' min for its result (result line ' || max(g.result_line) || ')'
FROM (SELECT *, sum(new) OVER (PARTITION BY file ORDER BY result_ts, line) AS grp
      FROM (SELECT *, coalesce(epoch(result_ts) - epoch(lag(result_ts) OVER (PARTITION BY file ORDER BY result_ts, line)) > 10, true)::INT AS new
            FROM tool_calls
            WHERE agent = 'claude' AND wait_s > {STALL_S}
              AND tool NOT IN {LONG_TOOLS})) g
JOIN sessions s USING (file)
GROUP BY g.file, g.grp;

-- 3a. Claude: the harness refused an edit because the file was never read or changed since.
INSERT INTO signals
SELECT 'edit_without_read', c.agent, s.session_id, s.project, c.file, c.line, c.ts,
       c.tool || ' on ' || coalesce(c.target_path, '?') || ': ' || left(c.error_text, 100)
FROM tool_calls c JOIN sessions s USING (file)
WHERE c.agent = 'claude' AND c.is_error
  AND regexp_matches(c.error_text, 'has not been read yet|modified since read');

-- 3b. Codex: apply_patch updates a file the session never read or wrote before.
INSERT INTO signals
SELECT 'edit_without_read', c.agent, s.session_id, s.project, c.file, c.line, c.ts,
       'apply_patch update on ' || c.target_path || ' with no earlier read in the session'
FROM tool_calls c JOIN sessions s USING (file)
WHERE c.agent = 'codex' AND c.tool = 'apply_patch' AND c.change_type = 'update'
  AND NOT EXISTS (SELECT 1 FROM file_reads r WHERE r.file = c.file AND r.line < c.line AND r.path = c.target_path)
  AND NOT EXISTS (SELECT 1 FROM tool_calls e WHERE e.file = c.file AND e.line < c.line
                  AND ((e.tool = 'apply_patch' AND e.target_path = c.target_path)
                       -- a shell command named the file (nl, cat, rg, a heredoc that wrote it)
                       OR (e.tool = 'exec_command' AND contains(e.command, regexp_extract(c.target_path, '[^/]+$')))));

-- 4. The last reply before the human speaks again (the handback) uses a reserved
--    word with no verifier pointer anywhere in it.
INSERT INTO signals
SELECT 'unverified_claim', t.agent, s.session_id, s.project, t.file, t.line, t.ts,
       '"' || regexp_extract(t.text, {lit(RESERVED)}, 0) || '" with no exit code, count or path:line: '
       || left(regexp_replace(t.text, '\\s+', ' ', 'g'), 160)
FROM (SELECT *, row_number() OVER (PARTITION BY file, grp ORDER BY line DESC) AS rk
      FROM (SELECT *, count(*) FILTER (WHERE human) OVER (PARTITION BY file ORDER BY line) AS grp
            FROM turns)
      WHERE role = 'assistant' AND kind IN ('text', 'final_answer') AND NOT is_sidechain
        AND length(text) > 0) t
JOIN sessions s USING (file)
WHERE t.rk = 1
  AND NOT EXISTS (SELECT 1 FROM turns p WHERE p.file = t.file AND p.human AND regexp_matches(p.text, {lit(PROBE_PROMPT)}))
  AND regexp_matches(regexp_replace(t.text, {lit(NOT_A_CLAIM)}, '', 'g'), {lit(RESERVED)})
  AND NOT regexp_matches(t.text, {lit(POINTER)});

-- 5. A human reply (not the opening prompt) that pushes back.
INSERT INTO signals
SELECT 'user_correction', t.agent, s.session_id, s.project, t.file, t.line, t.ts,
       left(regexp_replace(t.text, '\\s+', ' ', 'g'), 160)
FROM (SELECT *, row_number() OVER (PARTITION BY file ORDER BY line) AS k FROM turns WHERE human) t
JOIN sessions s USING (file)
WHERE t.k > 1
  AND ((regexp_matches(t.text, {lit(CORRECTION_START)}) AND NOT regexp_matches(t.text, {lit(NOT_CORRECTION)}))
       OR regexp_matches(t.text, {lit(CORRECTION_ANY)}));

-- Shapes used by 6-9, computed once.
CREATE OR REPLACE TEMP TABLE shaped AS
SELECT c.*, s.project, s.session_id AS sid, s.started_at,
       {shape('c.error_text', 160)} AS efp,
       {shape('c.command', 400)} AS cfp,
       {shape('c.command', 400, keep_paths=True)} AS cpath
FROM tool_calls c JOIN sessions s USING (file);

-- 6. A failure whose shape (tool + masked error text) an earlier session already
--    hit: the same lesson learned twice. One row per session and shape, cited at
--    its first occurrence in that session. User rejections, exit 130 and a bare
--    "Exit code N" are not lessons.
INSERT INTO signals
SELECT 'recurring_failure', f.agent, f.sid, f.project, f.file, f.line, f.ts,
       f.tool || ' failed as in ' || (f.k - 1) || ' earlier session(s), first ' || f.first_file || ':' || f.first_line
       || ' · shape: ' || left(regexp_replace(f.efp, '\\s+', ' ', 'g'), 120)
FROM (SELECT g.*, row_number() OVER (PARTITION BY tool, efp ORDER BY ts, file) AS k,
             first_value(file) OVER (PARTITION BY tool, efp ORDER BY ts, file) AS first_file,
             first_value(line) OVER (PARTITION BY tool, efp ORDER BY ts, file) AS first_line
      FROM (SELECT agent, sid, project, file, tool, efp, min(line) AS line, min(ts) AS ts
            FROM shaped
            WHERE is_error AND coalesce(exit_code, 0) <> 130 AND efp <> ''
              AND NOT regexp_matches(trim(efp), '^(exit code <n>|<p>)$')
              AND NOT regexp_matches(coalesce(error_text, ''), {lit(REJECTED)})
            GROUP BY ALL) g) f
WHERE f.k > 1;

-- 7. The same long command shape run in ACTION_SESSIONS or more sessions of one
--    project: a step worth writing once as a script, skill or instruction line.
--    One row per project and shape, cited where it becomes recurring.
INSERT INTO signals
SELECT 'recurring_action', r.agent, r.sid, r.project, r.file, r.line, r.ts,
       'same command in ' || r.n || ' sessions of ' || r.project || ' · shape: ' || left(regexp_replace(r.cfp, '\\s+', ' ', 'g'), 140)
FROM (SELECT g.*, row_number() OVER (PARTITION BY project, cfp ORDER BY ts, file) AS k,
             count(*) OVER (PARTITION BY project, cfp) AS n
      FROM (SELECT any_value(agent) AS agent, sid, project, file, cfp, min(line) AS line, min(ts) AS ts
            FROM shaped
            WHERE command IS NOT NULL AND project IS NOT NULL AND length(cfp) >= {ACTION_MIN}
            GROUP BY sid, project, file, cfp) g) r
WHERE r.k = {ACTION_SESSIONS};

-- 8. STREAK_MIN or more runs in a row of one check (same command, paths kept)
--    with the same exit code and error shape, while the session edited between
--    1 and STREAK_FILES files: edits that changed nothing the check sees.
INSERT INTO signals
SELECT 'no_progress_streak', g.agent, g.sid, g.project, g.file, g.l0, g.t0,
       g.n || ' runs in a row with the same result (exit ' || g.ec || ') while editing ' || g.ne || ' file(s) · shape: '
       || left(regexp_replace(g.cpath, '\\s+', ' ', 'g'), 140)
FROM (SELECT x.*, (SELECT count(DISTINCT e.target_path) FROM tool_calls e
                   WHERE e.file = x.file AND e.line > x.l0 AND e.line < x.l1 AND e.tool IN {EDIT_TOOLS}) AS ne
      FROM (SELECT any_value(agent) AS agent, any_value(sid) AS sid, any_value(project) AS project, file, cpath,
                   ec, efp, min(line) AS l0, max(line) AS l1, min(ts) AS t0, count(*) AS n
            FROM (SELECT *, coalesce(exit_code, -1) AS ec,
                         row_number() OVER (PARTITION BY file ORDER BY line)
                         - row_number() OVER (PARTITION BY file, cpath, coalesce(exit_code, -1), efp ORDER BY line) AS grp
                  FROM shaped WHERE command IS NOT NULL)
            GROUP BY file, cpath, ec, efp, grp
            HAVING count(*) >= {STREAK_MIN}) x) g
WHERE g.ne BETWEEN 1 AND {STREAK_FILES};

-- 9. One long call (same tool, byte-identical input of ARGS_MIN+ characters)
--    that succeeds ARGS_REPEAT or more times in a session: an argument loop.
--    Failures are repeated_failure's; long-running tools are skipped.
INSERT INTO signals
SELECT 'repeated_args', any_value(c.agent), any_value(s.session_id), any_value(s.project), c.file, min(c.line), min(c.ts),
       any_value(c.tool) || ' called ' || count(*) || 'x with the same ' || length(any_value(coalesce(c.input, c.command)))
       || '-character input · shape: ' || left(regexp_replace(any_value(coalesce(c.command, c.input)), '\\s+', ' ', 'g'), 120)
FROM tool_calls c JOIN sessions s USING (file)
WHERE NOT c.is_error AND length(coalesce(c.input, c.command, '')) >= {ARGS_MIN} AND c.tool NOT IN {LONG_TOOLS}
GROUP BY c.file, c.tool, md5(coalesce(c.input, c.command, ''))
HAVING count(*) >= {ARGS_REPEAT};

-- 10. A session with OUTLIER_LOOPS or more loop rows (repeated_failure,
--     no_progress_streak) that ran more than OUTLIER_X times the median turns of
--     its project's main sessions of OUTLIER_FLOOR+ turns: the loops cost real
--     time. Cited at the session's first loop row. Needs OUTLIER_SESSIONS such
--     sessions in the project.
INSERT INTO signals
SELECT 'loop_cost_outlier', any_value(g.agent), any_value(s.session_id), s.project, g.file, min(g.line), min(g.ts),
       any_value(s.n_turns) || ' turns, ' || round(any_value(s.n_turns) / any_value(m.med), 1) || 'x the median of '
       || s.project || coalesce(', $' || round(any_value(s.cost_usd), 2), '') || '; loops: '
       || string_agg(DISTINCT g.signal, ', ')
FROM signals g JOIN sessions s USING (file)
JOIN (SELECT project, median(n_turns) AS med, count(*) AS ns FROM sessions
      WHERE kind = 'main' AND n_turns >= {OUTLIER_FLOOR} GROUP BY 1) m ON m.project = s.project
WHERE g.signal IN ('repeated_failure', 'no_progress_streak') AND m.ns >= {OUTLIER_SESSIONS} AND m.med > 0
  AND s.n_turns > {OUTLIER_X} * m.med
GROUP BY g.file, s.project
HAVING count(*) >= {OUTLIER_LOOPS};
"""


def run_signals(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    con.execute(SQL)
    counts = dict(con.execute("SELECT signal, count(*) FROM signals GROUP BY 1").fetchall())
    return {s: counts.get(s, 0) for s in SIGNALS}
