# Rule signals

Five rules, no model. Each is one SQL statement in `src/tracelens/signals.py`. Every row cites `file:line` in the archive. Fixtures in `tests/fixtures/build.py` mark each expected positive (+) and negative (-).

| Signal | Fires on | Cites | Blind spots |
|---|---|---|---|
| `repeated_failure` | the same tool with byte-identical input fails 2+ times in one session | the first repeat | a retry with a trivially changed input is not caught |
| `approval_stall` | Claude: a call's result arrives > 20 min after the call and the tool is not one that legitimately blocks (`Agent`, `Task`, `TaskOutput`, `Monitor`, `Workflow`, `AskUserQuestion`, `ExitPlanMode`, `ScheduleWakeup`, `SendMessage`, `Skill`) | the first call of a parallel batch (results back within 10 s form one stall) | Codex records no approval events; a machine asleep mid-call also counts |
| `edit_without_read` | Claude: the harness rejected an `Edit`/`Write` with "has not been read yet" or "modified since read". Codex: `apply_patch` updates a path that no earlier read, patch, or shell command in the session named | the edit | Claude edits through Bash (`sed -i`) are not seen |
| `unverified_claim` | the last assistant reply before the next human turn (the handback) contains an ATTEST reserved word and no verifier pointer | the reply | a pointer is any of: exit code, `N/M` or `N of M`, `N passed`, `N tests`/`suites`/`checks`, `path:line`, `evidence:`, a backticked command followed by its result, `by \`cmd\``, a backticked commit hash, a link to a local file. A reply that cites a pointer for a different claim passes |
| `user_correction` | a human turn after the opening prompt that starts with no / nope / wrong (not "no need", "no luck", "no, that's fine", "X not needed"), is a bare "stop." / "stop it", or contains "I meant", "I said", "that's wrong", "not what I asked", "you're wrong" | the human turn | a declined proposal ("No. Park it.") still counts; a polite correction without these words does not. Telling the two apart needs the parked model signals |

Reserved words (ATTEST-100 §3): done, complete(d), fixed, resolved, passing, passes, green, works, working, correct, safe, secure, verified, confirmed, proven, no regressions, production-ready. Phrases like "working tree", "safe to", "not yet done", "tell me when it's done", "how it works", "the green … button" are removed before matching.
