# Transcript formats

Surveyed 2026-10-10 on this machine: Claude Code 3,002 files / 4.3 GB, Codex 14,681 files / 8.2 GB. Only the fields below reach the index. Everything else stays in the archive.

## Retention: why `archive` exists

Claude Code deletes transcripts under `~/.claude/projects/` once they are older than `cleanupPeriodDays`. The default is **30 days**; the minimum is 1. Since v2.1.248, transcripts of sessions started or last continued in Claude Desktop or Cowork are exempt, unless `desktopSessionCleanupPeriodDays` is set. Sources: code.claude.com/docs/en/claude-directory.md ("Cleaned up automatically"), code.claude.com/docs/en/data-usage.md, read 2026-10-10.

On this machine `cleanupPeriodDays` is not set in `~/.claude/settings.json` or `settings.local.json`, so CLI and scheduled sessions older than 30 days go. Files back to 2026-06-20 survive because Desktop sessions are exempt. Codex keeps `~/.codex/sessions/YYYY/MM/DD/` with no sweep found.

## Claude Code — `~/.claude/projects/<cwd-slug>/`

| Path | What it is | `sessions.kind` |
|---|---|---|
| `<session-uuid>.jsonl` | one session | `main` |
| `<session-uuid>/subagents/agent-<id>.jsonl` | one subagent run of that session | `subagent` |
| `<session-uuid>/subagents/workflows/wf_<id>/agent-<id>.jsonl` | a workflow agent | `subagent` |

Subagents are separate session rows linked by `parent_session_id`. Their records carry `isSidechain: true` and the parent's `sessionId`.

One JSON object per line. Record `type` values seen: `user`, `assistant`, `attachment`, `system`, `queue-operation`, `last-prompt`, `custom-title`, `ai-title`, `agent-name`, `mode`, `permission-mode`, `bridge-session`, `atis-latch`, `frame-link`, `file-history-snapshot`, `file-history-delta`, `cost-state`, `relocated`, `pr-link`, `started`, `result`, `artifact-*`.

Fields the index reads:

| Field | Used for |
|---|---|
| `type` | `user`, `assistant`, `system` become `turns` rows; the rest only count toward `sessions.n_records` |
| `uuid`, `parentUuid`, `isSidechain`, `sessionId`, `timestamp`, `cwd`, `gitBranch` | turn identity, time, project |
| `isMeta`, `isCompactSummary` | exclude injected user records from `human` |
| `message.model` | `turns.model`, `sessions.model` (most common) |
| `message.content` | a string (a typed prompt) or a list of blocks: `text`, `thinking`, `tool_use`, `tool_result`, `image`, `document` |
| `tool_use.id / name / input` | `tool_calls` row; `input.file_path`, `input.command`, `input.run_in_background` are pulled out |
| `tool_result.tool_use_id / is_error / content` | joins to the call; error text kept only when `is_error` |
| `toolUseResult.type` | `create` or `update` for `Write` |
| `subtype` (system) | `api_error`, `stop_hook_summary`, `compact_boundary`, `local_command`, … |
| `isApiErrorMessage`, `error` | `errors.kind = 'api'` |
| `totalCostUSD` (cost-state) | `sessions.cost_usd` (max) |

Exit codes are not a field. A failed Bash result's text starts `Exit code N`; the index parses N. A successful Bash call gets `exit_code = 0`.

Approval waits are not recorded either. The index stores `wait_s` = result timestamp minus call timestamp. A foreground Bash call cannot run past its 10-minute timeout cap, so a long wait on such a call means the session sat on a permission prompt.

After a compaction (`system` record with `subtype: compact_boundary`) Claude Code writes the kept history again with the same `uuid`s. The index keeps the first occurrence of each `uuid`, and of each `tool_use` / `tool_result` id. On 2026-10-10 that removed 19,087 replayed records in 23 files.

A `human` turn is a `user` record that is not sidechain, not meta, not a compact summary, carries no `tool_result`, has non-empty text, and does not start with `<` (slash-command echoes, task notifications, local command output).

## Codex — `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread-id>.jsonl`

One file per thread. Each line is `{timestamp, ordinal, type, payload}`. Types seen: `session_meta`, `turn_context`, `world_state`, `response_item`, `event_msg`, `token_usage_record`, `compacted`, `inter_agent_communication_metadata`. CLI versions 0.12 to 0.153.

| Field | Used for |
|---|---|
| `session_meta.payload.id / cwd / originator / thread_source / parent_thread_id / git.branch` | `sessions` row; `thread_source` in (`subagent`, `guardian_review`) makes `kind = 'subagent'` |
| `turn_context.payload.model` | `sessions.model` |
| `event_msg` with `payload.type = item_completed` | every turn and tool row; see below |
| `event_msg` with `payload.type = turn_aborted`, `payload.reason` | `errors.kind = 'aborted'` |

`item_completed.payload.item.type`:

| Item type | Becomes | Fields read |
|---|---|---|
| `UserMessage` | turn, role `user`, kind `prompt` | `content[*].text` |
| `AgentMessage` | turn, role `assistant`, kind = `phase` (`commentary` / `final_answer`) | `content[*].text`, `phase` |
| `Reasoning` | turn, kind `thinking`, no text (encrypted) | — |
| `CommandExecution` | tool call `exec_command` | `command[-1]`, `exit_code`, `status`, `duration.secs/nanos`, `aggregated_output` (on failure), `cwd`, `parsed_cmd[*]` |
| `FileChange` | one tool call `apply_patch` per changed path | `changes.<path>.type` (`add` / `update` / `delete`), `status` |
| `McpToolCall` | tool call `mcp__<server>__<tool>` | `arguments`, `status`, `result` |
| others (`WebSearch`, `CollabAgentToolCall`, `SubAgentActivity`, `ImageView`, `FunctionCallOutput`, …) | tool call named by item type | `status` |

`response_item` records duplicate the same messages and add the developer-role instruction injections. The index ignores them, so each message counts once.

`parsed_cmd` entries with `type = read` and a `path` become `file_reads` rows, resolved against the command's `cwd`.

`approval_policy` is `never` on 96% of turn contexts and the format records no approval event. Approval stalls are therefore Claude-only.

## Index tables

All rows carry `file` (archive-relative path) and `line` (1-based, as `sed -n Np`), so any row cites its source line.

| Table | Grain |
|---|---|
| `sessions` | one JSONL file |
| `turns` | one message record, replays dropped (Claude user/assistant/system; Codex user/agent/reasoning item) |
| `tool_calls` | one tool call (Codex: one per patched path) with its result |
| `file_reads` | one file read (Claude `Read`, Codex `parsed_cmd` read) |
| `errors` | failed tool calls, API errors, aborted turns |
| `signals` | written by `tracelens signals`; see [signals.md](signals.md) |
| `index_meta` | build time and row counts |

## Hand count, 2026-10-10

An independent counter (`scripts/handcount.py`: plain `json`, no DuckDB, no shared code) matched the index on turns, tool calls and errors for 3 sessions per agent: a Claude main session, a Claude worktree session, a Claude subagent, a Codex `codex_exec` run, a Codex Desktop session and a Codex subagent. Six of six matched.
