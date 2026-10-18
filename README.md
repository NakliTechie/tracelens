<h1 align="center">tracelens</h1>

<p align="center"><b>Your Claude Code and Codex transcripts in one local DuckDB file, with five rule signals that point at the exact JSONL line where a session went wrong.</b></p>

<p align="center">One CLI. macOS. No server, no account, no model; data stays on your disk.</p>

**Tier: Tool.** One CLI core; the [ntkit](https://github.com/NakliTechie/ntkit) skill `/traces-nt` is the agent face: it reads the index and writes ranked findings to `plan/`.

## Install

| Path | Command |
|---|---|
| uv (from GitHub) | `uv tool install git+https://github.com/NakliTechie/tracelens.git` |
| uv (from a clone) | `uv tool install .` |

Pick a data directory and run the pipeline once. The archive is an APFS clone of `~/.claude/projects` and `~/.codex/sessions`, so it takes seconds and almost no disk. The index takes about a minute for 12 GB.

```bash
export TRACELENS_HOME=~/Code/tracelens   # holds archive/ and tracelens.duckdb
tracelens archive && tracelens index && tracelens report
```

Re-run `tracelens archive` whenever you like; it copies only new or changed files. No config file, no account. To try it without your own data, run `uv run pytest` in a clone: it indexes the synthetic fixtures in `tests/fixtures/`.

## Why

You have thousands of agent sessions on disk and you have read none of them. Claude Code deletes CLI transcripts after 30 days (`cleanupPeriodDays`), so the evidence of what keeps going wrong disappears before anyone looks.

tracelens keeps a copy, normalises both formats into one DuckDB file, and runs rules that need no model: the same command failing again, a session stuck on a permission prompt, an edit to a file the agent never read, a success claim with no verifier, a human saying "no". Every row cites `file:line`. LiteLLM Lens (`BerriAI/lens`) showed the loop; tracelens does it with DuckDB over files you already have, instead of ClickHouse and OTLP.

## What you get

`tracelens report` prints session and signal counts by agent and week, the projects with the most signal rows, and the top sessions per signal with a `file:line` citation. Open the cited line with `sed -n '<line>p' archive/<file>`, or query the tables directly: `duckdb tracelens.duckdb`. The tables and the fields they come from are in [docs/formats.md](docs/formats.md). The rule definitions and their blind spots are in [docs/signals.md](docs/signals.md).

The weekly table is the measure: after a fix to a CLAUDE.md line, a playbook or an allow-rule lands, the signal rate for that week should drop.

## Privacy

Transcripts hold pasted tokens and personal data. `archive/` and `*.duckdb` are gitignored. tracelens never reads `~/.claude` or `~/.codex` beyond the JSONL trees, never modifies them, and sends nothing off the machine. The test fixtures are synthetic.

## Commands

```bash
tracelens status     # one read: archive size, index age, row and signal counts, next command
tracelens archive    # incremental copy of both transcript trees into archive/
tracelens index      # rebuild tracelens.duckdb from archive/ (atomic)
tracelens signals    # rerun the five rule signals into the signals table
tracelens report     # counts by agent / week / project, top sessions per signal (--top N)
tracelens manifest   # every command, its effect and exit codes, as JSON
```

Every command takes `--json` and `--home`. `tracelens manifest` is the agent face; the contract it answers to is [docs/agent-contract.md](docs/agent-contract.md).

## Verify it yourself

```bash
uv run pytest                                               # fixtures: exact signal rows, row counts, archive idempotence
TRACELENS_HOME=. python3 scripts/handcount.py <paths.txt>   # independent per-file count vs the index
```

`test_signals_exact` fails on any missing or extra signal row against the marked fixtures. On 2026-10-10 the hand count matched the index on 3 sessions per agent, and a second `archive` run copied 0 of 17,683 files.

## License

MIT, see [LICENSE](LICENSE). · Formats: [docs/formats.md](docs/formats.md) · Signals: [docs/signals.md](docs/signals.md)
