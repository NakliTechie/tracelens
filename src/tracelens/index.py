"""Build tracelens.duckdb from archive/<agent>/**/*.jsonl.

DuckDB does the parsing: read_text loads a batch of files, string_split
numbers the lines (1-based, same as `sed -n Np`), and json_extract pulls the
fields listed in docs/formats.md. Every table row carries (file, line) so any
finding can cite the exact JSONL line.

The database is rebuilt into a temp file and renamed into place, so an
interrupted run leaves the previous index untouched.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import duckdb

from .paths import AGENTS

BATCH_BYTES = 256 * 1024 * 1024
TEXT_CAP = 20_000  # chars of prompt / reply text kept per turn
INPUT_CAP = 4_000  # chars of tool input JSON kept per call
ERR_CAP = 1_000  # chars of error output kept per failed call

SCHEMA = """
CREATE TABLE sessions (
  agent VARCHAR, file VARCHAR PRIMARY KEY, session_id VARCHAR, parent_session_id VARCHAR,
  kind VARCHAR, project VARCHAR, cwd VARCHAR, git_branch VARCHAR, originator VARCHAR,
  model VARCHAR, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, n_records BIGINT,
  n_turns BIGINT, n_prompts BIGINT, n_tool_calls BIGINT, n_tool_errors BIGINT, cost_usd DOUBLE);
CREATE TABLE turns (
  agent VARCHAR, file VARCHAR, line INTEGER, ts TIMESTAMPTZ, session_id VARCHAR,
  role VARCHAR, kind VARCHAR, is_sidechain BOOLEAN, human BOOLEAN, uuid VARCHAR,
  parent_uuid VARCHAR, model VARCHAR, text VARCHAR);
CREATE TABLE tool_calls (
  agent VARCHAR, file VARCHAR, line INTEGER, ts TIMESTAMPTZ, session_id VARCHAR,
  call_id VARCHAR, tool VARCHAR, input VARCHAR, target_path VARCHAR, command VARCHAR,
  background BOOLEAN, result_line INTEGER, result_ts TIMESTAMPTZ, is_error BOOLEAN,
  exit_code INTEGER, error_text VARCHAR, wait_s DOUBLE, duration_s DOUBLE, change_type VARCHAR);
CREATE TABLE file_reads (
  agent VARCHAR, file VARCHAR, line INTEGER, ts TIMESTAMPTZ, path VARCHAR);
CREATE TABLE errors (
  agent VARCHAR, file VARCHAR, line INTEGER, ts TIMESTAMPTZ, session_id VARCHAR,
  kind VARCHAR, tool VARCHAR, exit_code INTEGER, message VARCHAR);
CREATE TABLE index_meta (key VARCHAR, value VARCHAR);
"""

# Split each file into (file, line, json) rows; drop blank and torn lines.
RAW = """
CREATE OR REPLACE TEMP TABLE raw AS
WITH f AS (SELECT filename, content FROM read_text($files)),
l AS (
  SELECT filename, unnest(list_transform(string_split(content, chr(10)),
                                         lambda x, i: {'n': i, 'x': x})) AS r
  FROM f)
SELECT substr(filename, $strip) AS file, r.n::INTEGER AS line, r.x AS j
FROM l WHERE length(r.x) > 1 AND json_valid(r.x)
"""

# ---------------------------------------------------------------- Claude Code
CLAUDE_STAGE = f"""
CREATE OR REPLACE TEMP TABLE c AS
SELECT file, line, v[1] AS type, v[2] AS uuid, v[3] AS parent_uuid,
       coalesce(v[4] = 'true', false) AS is_sidechain, v[5] AS session_id,
       try_cast(v[6] AS TIMESTAMPTZ) AS ts, v[7] AS cwd, v[8] AS git_branch,
       coalesce(v[9] = 'true', false) AS is_meta, v[10] AS model, v[11] AS subtype,
       coalesce(v[12] = 'true', false) AS is_api_error, v[13] AS result_type,
       try_cast(v[14] AS DOUBLE) AS cost_usd, coalesce(v[15] = 'true', false) AS is_compact,
       left(coalesce(v[16], v[17]), {ERR_CAP}) AS sys_error, content
FROM (
  SELECT file, line,
    json_extract_string(j, ['$.type', '$.uuid', '$.parentUuid', '$.isSidechain', '$.sessionId',
      '$.timestamp', '$.cwd', '$.gitBranch', '$.isMeta', '$.message.model', '$.subtype',
      '$.isApiErrorMessage', '$.toolUseResult.type', '$.totalCostUSD', '$.isCompactSummary',
      '$.error', '$.content']) AS v,
    CASE WHEN j LIKE '%"message"%' THEN json_extract(j, '$.message.content') END AS content
  FROM raw);

CREATE OR REPLACE TEMP TABLE ct AS
SELECT *,
  CASE json_type(content)
    WHEN 'VARCHAR' THEN content ->> '$'
    WHEN 'ARRAY' THEN array_to_string(json_extract_string(content, '$[*].text'), chr(10))
  END AS text,
  CASE WHEN json_type(content) = 'ARRAY' THEN json_extract_string(content, '$[*].type') END AS block_types
FROM c WHERE type IN ('user', 'assistant', 'system');
"""

CLAUDE_INSERT = f"""
INSERT INTO turns
SELECT 'claude', file, line, ts, session_id,
  type AS role,
  CASE WHEN type = 'system' THEN coalesce(subtype, 'system')
       WHEN type = 'user' AND list_contains(block_types, 'tool_result') THEN 'tool_result'
       WHEN type = 'user' THEN 'prompt'
       ELSE coalesce(block_types[1], 'text') END AS kind,
  is_sidechain,
  type = 'user' AND NOT is_sidechain AND NOT is_meta AND NOT is_compact
    AND NOT coalesce(list_contains(block_types, 'tool_result'), false)
    AND length(trim(coalesce(text, ''))) > 0 AND NOT starts_with(ltrim(text), '<') AS human,
  uuid, parent_uuid, model, left(text, {TEXT_CAP})
FROM ct;

CREATE OR REPLACE TEMP TABLE cu AS
SELECT file, line, ts, session_id, b ->> 'id' AS call_id, b ->> 'name' AS tool, b -> 'input' AS input
FROM (SELECT file, line, ts, session_id, unnest(json_extract(content, '$[*]')) AS b
      FROM ct WHERE type = 'assistant' AND list_contains(block_types, 'tool_use'))
WHERE b ->> 'type' = 'tool_use';

CREATE OR REPLACE TEMP TABLE cr AS
SELECT file, line, ts, result_type, b ->> 'tool_use_id' AS call_id,
  coalesce(b ->> 'is_error' = 'true', false) AS is_error,
  CASE WHEN b ->> 'is_error' = 'true' THEN left(CASE json_type(b -> 'content')
      WHEN 'VARCHAR' THEN b ->> 'content'
      ELSE array_to_string(json_extract_string(b, '$.content[*].text'), chr(10)) END, {ERR_CAP})
  END AS error_text
FROM (SELECT file, line, ts, result_type, unnest(json_extract(content, '$[*]')) AS b
      FROM ct WHERE type = 'user' AND list_contains(block_types, 'tool_result'))
WHERE b ->> 'type' = 'tool_result';

INSERT INTO tool_calls
SELECT 'claude', u.file, u.line, u.ts, u.session_id, u.call_id, u.tool,
  left(u.input::VARCHAR, {INPUT_CAP}),
  coalesce(u.input ->> 'file_path', u.input ->> 'notebook_path', u.input ->> 'path'),
  u.input ->> 'command',
  coalesce(u.input ->> 'run_in_background' = 'true', false),
  r.line, r.ts, r.is_error,
  CASE WHEN r.is_error THEN try_cast(regexp_extract(r.error_text, '^Exit code (\\d+)', 1) AS INTEGER)
       WHEN u.tool = 'Bash' AND r.line IS NOT NULL THEN 0 END,
  r.error_text,
  epoch(r.ts) - epoch(u.ts), NULL,
  CASE WHEN u.tool = 'Write' THEN r.result_type END
FROM cu u LEFT JOIN cr r ON r.file = u.file AND r.call_id = u.call_id;

INSERT INTO file_reads
SELECT 'claude', file, line, ts, input ->> 'file_path' FROM cu WHERE tool = 'Read';

INSERT INTO errors
SELECT 'claude', file, line, ts, session_id, 'api', NULL, NULL, coalesce(sys_error, left(text, {ERR_CAP}))
FROM ct WHERE (type = 'system' AND subtype = 'api_error') OR (type = 'assistant' AND is_api_error);
"""

CLAUDE_SESSIONS = """
INSERT INTO sessions
SELECT 'claude', c.file,
  CASE WHEN c.file LIKE '%/subagents/%' THEN regexp_extract(c.file, 'agent-([^/]+)\\.jsonl$', 1)
       ELSE regexp_extract(c.file, '([^/]+)\\.jsonl$', 1) END,
  CASE WHEN c.file LIKE '%/subagents/%' THEN regexp_extract(c.file, '/([^/]+)/subagents/', 1) END,
  CASE WHEN c.file LIKE '%/subagents/%' THEN 'subagent' ELSE 'main' END,
  NULL, first(c.cwd ORDER BY c.line) FILTER (WHERE c.cwd IS NOT NULL),
  first(c.git_branch ORDER BY c.line) FILTER (WHERE c.git_branch IS NOT NULL), 'claude-code',
  mode(c.model) FILTER (WHERE c.model IS NOT NULL AND c.model <> '<synthetic>'),
  min(c.ts), max(c.ts), count(*), 0, 0, 0, 0, max(c.cost_usd)
FROM c GROUP BY c.file;
"""

# ---------------------------------------------------------------------- Codex
CODEX_STAGE = f"""
CREATE OR REPLACE TEMP TABLE x AS
SELECT file, line, try_cast(v[1] AS TIMESTAMPTZ) AS ts, v[2] AS type, v[3] AS ptype,
  v[4] AS item_type, v[5] AS item_id, try_cast(v[6] AS INTEGER) AS exit_code, v[7] AS status,
  try_cast(v[8] AS DOUBLE) + coalesce(try_cast(v[9] AS DOUBLE), 0) / 1e9 AS duration_s,
  v[10] AS command, regexp_replace(v[11], '^file://', '') AS item_cwd, v[12] AS phase,
  v[13] AS server, v[14] AS mcp_tool, v[15] AS output, v[16] AS reason, v[17] AS model, j
FROM (
  SELECT file, line, j, json_extract_string(j, ['$.timestamp', '$.type', '$.payload.type',
    '$.payload.item.type', '$.payload.item.id', '$.payload.item.exit_code', '$.payload.item.status',
    '$.payload.item.duration.secs', '$.payload.item.duration.nanos', '$.payload.item.command[#-1]',
    '$.payload.item.cwd', '$.payload.item.phase', '$.payload.item.server', '$.payload.item.tool',
    '$.payload.item.aggregated_output', '$.payload.reason', '$.payload.model']) AS v
  FROM raw);

CREATE OR REPLACE TEMP TABLE xs AS
SELECT file,
  first(m[1] ORDER BY line) AS session_id, first(m[2] ORDER BY line) AS cwd,
  first(m[3] ORDER BY line) AS originator, first(m[4] ORDER BY line) AS thread_source,
  first(m[5] ORDER BY line) AS parent_id, first(m[6] ORDER BY line) AS git_branch
FROM (SELECT file, line, json_extract_string(j, ['$.payload.id', '$.payload.cwd', '$.payload.originator',
        '$.payload.thread_source', '$.payload.parent_thread_id', '$.payload.git.branch']) AS m
      FROM x WHERE type = 'session_meta')
GROUP BY file;

CREATE OR REPLACE TEMP TABLE xi AS
SELECT x.*, xs.session_id, xs.thread_source, xs.cwd AS session_cwd
FROM x LEFT JOIN xs USING (file) WHERE x.ptype = 'item_completed';
"""

CODEX_INSERT = f"""
INSERT INTO turns
SELECT 'codex', file, line, ts, session_id,
  CASE item_type WHEN 'UserMessage' THEN 'user' ELSE 'assistant' END,
  CASE item_type WHEN 'UserMessage' THEN 'prompt' WHEN 'Reasoning' THEN 'thinking'
       ELSE coalesce(phase, 'text') END,
  coalesce(thread_source IN ('subagent', 'guardian_review'), false),
  item_type = 'UserMessage' AND coalesce(thread_source NOT IN ('subagent', 'guardian_review'), true),
  item_id, NULL, NULL,
  CASE WHEN item_type <> 'Reasoning'
       THEN left(array_to_string(json_extract_string(j, '$.payload.item.content[*].text'), chr(10)), {TEXT_CAP}) END
FROM xi WHERE item_type IN ('UserMessage', 'AgentMessage', 'Reasoning');

INSERT INTO tool_calls
SELECT 'codex', file, line, ts, session_id, item_id,
  CASE item_type WHEN 'CommandExecution' THEN 'exec_command'
                 WHEN 'McpToolCall' THEN 'mcp__' || server || '__' || mcp_tool ELSE item_type END,
  CASE WHEN item_type = 'CommandExecution' THEN left(command, {INPUT_CAP})
       ELSE left(json_extract(j, '$.payload.item.arguments')::VARCHAR, {INPUT_CAP}) END,
  NULL, command, false, line, ts,
  status = 'failed' OR coalesce(exit_code, 0) <> 0,
  exit_code,
  CASE WHEN status = 'failed' OR coalesce(exit_code, 0) <> 0
       THEN left(coalesce(output, json_extract(j, '$.payload.item.result')::VARCHAR), {ERR_CAP}) END,
  0, duration_s, NULL
FROM xi WHERE item_type NOT IN ('UserMessage', 'AgentMessage', 'Reasoning', 'FileChange', 'ContextCompaction');

-- one row per changed path, so edit checks work per file
INSERT INTO tool_calls
SELECT 'codex', file, line, ts, session_id, item_id || ':' || k, 'apply_patch', NULL, k, NULL, false,
  line, ts, status = 'failed', NULL,
  CASE WHEN status = 'failed' THEN left(json_extract_string(j, '$.payload.item.stderr'), {ERR_CAP}) END,
  0, NULL, json_extract_string(j, '$.payload.item.changes."' || k || '".type')
FROM (SELECT *, unnest(json_keys(j, '$.payload.item.changes')) AS k FROM xi WHERE item_type = 'FileChange');

INSERT INTO file_reads
SELECT 'codex', file, line, ts,
  CASE WHEN starts_with(p, '/') THEN p ELSE coalesce(item_cwd, session_cwd) || '/' || p END
FROM (SELECT file, line, ts, item_cwd, session_cwd, pc ->> 'type' AS pt, pc ->> 'path' AS p
      FROM (SELECT *, unnest(json_extract(j, '$.payload.item.parsed_cmd[*]')) AS pc
            FROM xi WHERE item_type = 'CommandExecution'))
WHERE pt = 'read' AND p IS NOT NULL;

INSERT INTO errors
SELECT 'codex', x.file, x.line, x.ts, xs.session_id, 'aborted', NULL, NULL, x.reason
FROM x LEFT JOIN xs USING (file) WHERE x.ptype = 'turn_aborted';

INSERT INTO sessions
SELECT 'codex', x.file, any_value(xs.session_id), any_value(xs.parent_id),
  CASE WHEN any_value(xs.thread_source) IN ('subagent', 'guardian_review') THEN 'subagent' ELSE 'main' END,
  NULL, any_value(xs.cwd), any_value(xs.git_branch), any_value(xs.originator),
  mode(x.model) FILTER (WHERE x.model IS NOT NULL),
  min(x.ts), max(x.ts), count(*), 0, 0, 0, 0, NULL
FROM x LEFT JOIN xs USING (file) GROUP BY x.file;
"""

# ------------------------------------------------------------- after all files
FINALISE = """
INSERT INTO errors
SELECT agent, file, line, ts, session_id, 'tool', tool, exit_code, error_text
FROM tool_calls WHERE is_error;

UPDATE sessions s SET
  project = coalesce(nullif(regexp_extract(
      regexp_replace(regexp_replace(s.cwd, '/\\.(claude/)?worktrees/.*$', ''), '/+$', ''),
      '([^/]+)$', 1), ''), '(unknown)'),
  n_turns = coalesce(t.n_turns, 0), n_prompts = coalesce(t.n_prompts, 0),
  n_tool_calls = coalesce(k.n_calls, 0), n_tool_errors = coalesce(k.n_err, 0)
FROM sessions s2
LEFT JOIN (SELECT file, count(*) AS n_turns, count(*) FILTER (WHERE human) AS n_prompts
           FROM turns GROUP BY file) t ON t.file = s2.file
LEFT JOIN (SELECT file, count(*) AS n_calls, count(*) FILTER (WHERE is_error) AS n_err
           FROM tool_calls GROUP BY file) k ON k.file = s2.file
WHERE s.file = s2.file;

-- Claude subagent files carry no cwd of their own in some versions; inherit the parent's project.
UPDATE sessions s SET project = p.project
FROM sessions p
WHERE s.agent = 'claude' AND s.kind = 'subagent' AND s.project = '(unknown)'
  AND p.agent = 'claude' AND p.kind = 'main' AND p.session_id = s.parent_session_id;

-- Claude Code scratchpads live at /private/tmp/claude-<uid>/<slug>/..., where <slug> is the
-- owning cwd with / and . turned into -. Map them back to that cwd's project.
UPDATE sessions s SET project = m.project
FROM (SELECT regexp_replace(cwd, '[/.]', '-', 'g') AS slug, any_value(project) AS project
      FROM sessions WHERE cwd IS NOT NULL AND NOT starts_with(cwd, '/private/tmp/') GROUP BY 1) m
WHERE regexp_extract(s.cwd, '^/private/tmp/claude-\\d+/([^/]+)/', 1) = m.slug;

CREATE INDEX turns_file ON turns (file, line);
CREATE INDEX calls_file ON tool_calls (file, line);
"""


def _batches(files: list[Path]) -> list[list[Path]]:
    out, cur, size = [], [], 0
    for f in files:
        n = f.stat().st_size
        if cur and size + n > BATCH_BYTES:
            out.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += n
    if cur:
        out.append(cur)
    return out


def build_index(archive: Path, db: Path, progress=None) -> dict:
    t0 = time.time()
    tmp = db.with_name(db.name + ".tmp")
    for p in (tmp, Path(str(tmp) + ".wal")):
        p.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute("SET TimeZone = 'Asia/Kolkata'")
    con.execute(SCHEMA)
    strip = len(str(archive)) + 2  # 1-based substr past "<archive>/"
    stats = {"files": 0, "bytes": 0}
    for agent in AGENTS:
        root = archive / agent
        files = sorted(root.rglob("*.jsonl")) if root.is_dir() else []
        stage, insert = (CLAUDE_STAGE, CLAUDE_INSERT + CLAUDE_SESSIONS) if agent == "claude" else (CODEX_STAGE, CODEX_INSERT)
        for i, batch in enumerate(_batches(files)):
            con.execute(RAW, {"files": [str(f) for f in batch], "strip": strip})
            con.execute(stage)
            con.execute(insert)
            stats["files"] += len(batch)
            stats["bytes"] += sum(f.stat().st_size for f in batch)
            if progress:
                progress(f"{agent}: {stats['files']} files indexed")
    con.execute(FINALISE)
    for t in ("sessions", "turns", "tool_calls", "file_reads", "errors"):
        stats[t] = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    stats["seconds"] = round(time.time() - t0, 1)
    con.executemany("INSERT INTO index_meta VALUES (?, ?)",
                    [(k, str(v)) for k, v in stats.items()] + [("built_at", time.strftime("%Y-%m-%dT%H:%M:%S%z"))])
    con.close()
    os.replace(tmp, db)
    return stats
