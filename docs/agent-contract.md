# §0 Agent contract

The agent that drives tracelens is the `/traces-nt` skill (Batch B), or any agent with a shell. It is context-poor, can die mid-turn, and must not read 12 GB of transcripts. tracelens is built for that user.

| DRIVER check | tracelens answer |
|---|---|
| Single perception act | `tracelens status --json`: archive size per agent, index build time and row counts, signal counts, and `next` (the one command to run now, or null). |
| Closed vocabularies, structured output | Every command takes `--json`. Exit codes: 0 ok, 1 partial (some source files not copied), 2 usage, 3 missing prerequisite with a `remedy` command. Signal names are a closed set of 5. Error kinds are `tool`, `api`, `aborted`. |
| Output-size bound | `report` grows with weeks × agents, `--top` and the 15-project cap. Never with archive size. Signal details cap at 160 chars. Deep reads go through SQL on the DuckDB file. |
| Crash safety | `archive` copies to a temp name and renames; an interrupted run leaves whole files only, and a re-run resumes. `index` builds `tracelens.duckdb.tmp` and renames over the old file. `signals` replaces one table. Every command is safe to re-run. |
| Where the trajectory lives | The archive is append-only (sources that vanish stay). `index_meta` records each build. The weekly table in `report` is the before/after view for a fix. |
| Accretion mechanism | A false positive or miss found in the wild becomes a fixture line in `tests/fixtures/build.py` with a `+`/`-` mark, and `test_signals_exact` locks it. |
| Layer tower | 1 archive (raw JSONL, cites by path) → 2 index (tables, cite by `file:line`) → 3 signals (rules over tables) → 4 report (counts over signals) → 5 `/traces-nt` (findings over all of it). Each layer reads only the one below. |
| Evaluator boundary | Signals are SQL over the index; no model judges anything. The fixture test is the judge of the rules, and the agent under study cannot write either. A missing table makes `report` re-run signals, never print stale or empty counts as clean. |

## Two doors, one core

`tracelens manifest` prints every command with its effect, prerequisite and exit codes. There is no UI; the CLI is the only door, and the manifest is its agent face.

Person-only acts, named explicitly: deleting `archive/` or `tracelens.duckdb`, and sending any transcript content off this machine.
