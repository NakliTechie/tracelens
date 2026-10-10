# tracelens

Local trace investigator for your own Claude Code and Codex sessions: archive the JSONL, query it in DuckDB, flag recurring failures, and turn them into fixes for your harness.

**Tier: Tool.** One CLI core, one ntkit skill (`/traces-nt`) as the agent face. No server, no account; data stays on your disk.

## Install

TODO: `uv tool install` path once the CLI exists (Batch A).

## Why

Lots of agent traces pile up across Claude Code (`~/.claude/projects/**/*.jsonl`) and Codex (`~/.codex/sessions/**/*.jsonl`): ~3,000 + ~14,700 files, ~12 GB on 2026-10-10. Nobody reads them. LiteLLM Lens (`BerriAI/lens`) showed the loop: record every run, let an investigator find what went wrong, feed it back, measure the next run. Lens needs Docker + ClickHouse + OTLP. These traces are already JSONL on disk, so DuckDB over the files is enough.

## Shape

1. **Archive** — copy both JSONL trees into a local archive so Claude Code's transcript cleanup can't delete history.
2. **Index** — normalise both formats into one DuckDB file: session, project, agent, turn, tool, args, exit code, error, cost, timestamps.
3. **Rule signals (no model)** — repeated failing tool calls, approval stalls > 20 min, edit without re-read, ATTEST reserved words without a verifier, user corrections ("no", "stop", "that's wrong").
4. **Typed-decision signals (later)** — fuzzy questions ("claimed success without checking", "drifted off task") through a Jev-style decisions model; Jev API first, a local small model if LocalMind pending Step 6 says one holds.
5. **Investigate** — `/traces-nt` runs an agent over the DuckDB with SQL, clusters failures into findings that cite session id + line, and names a fix target (CLAUDE.md line, playbook, skill text, allow-rule). Writes to `plan/`; never edits CLAUDE.md without a yes.
6. **Measure** — signal rate per week before vs after a fix lands.

Removable AI: steps 1–3 and 6 work with no model.

## Privacy

Transcripts hold pasted tokens and personal data. The archive and DuckDB file stay local and are gitignored. Anything sent to a remote model is redacted first.

## Context

Designed in the knowledge-vault session of 2026-10-10 after capturing LiteLLM Lens (vault: `sources/2026-10-08-berriai-lens-repo.md`). Related vault notes: `sources/2026-09-26-annabellschfr-jev-named-dimension-trace-embeddings.md` (named-dimension Jev signals), `sources/2026-09-30-llmluthor-your-own-jev-thread.md` (local step judge from traces), `topics/harness-craft.md`.
