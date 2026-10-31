"""Generate the synthetic fixture archive. All content is invented.

Run: uv run python tests/fixtures/build.py
Each record's comment names the signal it should (+) or should not (-) fire.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent / "archive"
T0 = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
SID = "11111111-1111-4111-8111-111111111111"
CWD = "/tmp/fx/alpha"


def ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def cc(kind: str, minutes: float, content, **extra) -> dict:
    rec = {"type": kind, "uuid": f"u{minutes}", "parentUuid": None, "isSidechain": False,
           "sessionId": SID, "timestamp": ts(minutes), "cwd": CWD, "gitBranch": "main",
           "message": {"role": kind, "content": content}}
    if kind == "assistant":
        rec["message"]["model"] = "claude-test-1"
    rec.update(extra)
    return rec


def call(i: str, name: str, inp: dict) -> list:
    return [{"type": "tool_use", "id": i, "name": name, "input": inp}]


def result(i: str, text: str, err: bool = False) -> list:
    return [{"type": "tool_result", "tool_use_id": i, "content": text, "is_error": err}]


def text(s: str) -> list:
    return [{"type": "text", "text": s}]


claude = [
    cc("user", 0, "No tests exist yet; add them."),                                 # 1  - correction (opening prompt)
    cc("assistant", 1, call("t1", "Bash", {"command": "pytest"})),                  # 2
    cc("user", 1.1, result("t1", "Exit code 1\nFAILED test_x", True)),              # 3
    cc("assistant", 2, call("t2", "Bash", {"command": "pytest"})),                  # 4  + repeated_failure
    cc("user", 2.1, result("t2", "Exit code 1\nFAILED test_x", True)),              # 5
    cc("assistant", 3, call("t3", "Bash", {"command": "pytest -x"})),               # 6  - repeated (other input)
    cc("user", 3.1, result("t3", "Exit code 1", True)),                             # 7
    cc("assistant", 4, call("t4", "Bash", {"command": "ls"})),                      # 8  + approval_stall
    cc("user", 29, result("t4", "a.py\nb.py")),                                     # 9
    cc("assistant", 30, call("t5", "Agent", {"prompt": "review"})),                 # 10 - stall (subagent)
    cc("user", 60, result("t5", "review done")),                                    # 11
    cc("assistant", 61, call("t6", "Bash", {"command": "sleep 1", "run_in_background": True})),  # 12 + approval_stall (background returns at once)
    cc("user", 90, result("t6", "started")),                                        # 13
    cc("assistant", 91, call("t7", "Edit", {"file_path": "/tmp/fx/alpha/a.py", "old_string": "x", "new_string": "y"})),  # 14 + edit_without_read
    cc("user", 91.1, result("t7", "<tool_use_error>File has not been read yet. Read it first before writing to it.</tool_use_error>", True)),  # 15
    cc("assistant", 92, call("t8", "Read", {"file_path": "/tmp/fx/alpha/b.py"})),   # 16
    cc("user", 92.1, result("t8", "1\tprint('b')")),                                # 17
    cc("assistant", 93, call("t9", "Edit", {"file_path": "/tmp/fx/alpha/b.py", "old_string": "b", "new_string": "c"})),  # 18 - edit (read first)
    cc("user", 93.1, result("t9", "ok"), toolUseResult={"filePath": "/tmp/fx/alpha/b.py"}),  # 19
    cc("assistant", 94, text("I am looking at the tests.")),                        # 20 - claim (not last in turn)
    cc("assistant", 95, text("All tests pass now. Done.")),                         # 21 + unverified_claim
    cc("user", 96, "no, that's wrong: test_x still fails"),                         # 22 + user_correction
    cc("assistant", 97, text("Fixed. `pytest` exit 0, 12/12 passed.")),             # 23 - claim (pointer)
    cc("user", 98, "Nobody uses that flag, drop it."),                              # 24 - correction ("Nobody")
    cc("assistant", 99, text("Dropped it. The working tree is clean.")),            # 25 - claim (working tree)
    cc("user", 100, [{"type": "text", "text": "<command-name>/clear</command-name>"}], isMeta=True),  # 26 - not human
    cc("user", 101, "stop. Write the docs instead."),                               # 27 + user_correction
    cc("assistant", 102, text("Docs written: [usage](docs/usage.md) is done.")),    # 28 - claim (file link)
    {"type": "system", "subtype": "api_error", "uuid": "s1", "isSidechain": False, "sessionId": SID,
     "timestamp": ts(103), "cwd": CWD, "error": "overloaded", "level": "error"},     # 29 api error
    {"type": "cost-state", "sessionId": SID, "totalCostUSD": 0.42},                 # 30
    cc("assistant", 110, call("p1", "Bash", {"command": "git pull"})),              # 31 + approval_stall (one row for 31+32)
    cc("assistant", 110.5, call("p2", "Read", {"file_path": "/tmp/fx/alpha/c.py"})),  # 32 - parallel sibling of 31
    cc("user", 135, result("p1", "Already up to date.")),                           # 33
    cc("user", 135.01, result("p2", "1\tpass")),                                   # 34
    cc("user", 139, "no need to poll hourly, once a day is enough"),               # 35 - correction (declines an offer)
    cc("user", 140, "no, that's fine. push it."),                                  # 36 - correction (agreement)
    cc("user", 141, "stop ollama"),                                                # 37 - correction (an instruction)
    cc("user", 142, "Ship it. I meant the staging branch, not main."),             # 38 + user_correction
    cc("assistant", 143, text("Final gate: GREEN, 733 tests, ruff clean.")),        # 39 - claim (test count)
    cc("user", 144, "ok, next"),                                                   # 40
    cc("assistant", 145, text("`make check` green, pushed.")),                      # 41 - claim (command + result)
    cc("user", 146, "ok, next"),                                                   # 42
    cc("assistant", 147, text("Verified: pushed as `8d3fd8b`.")),                   # 43 - claim (commit hash)
    cc("user", 148, "ok, next"),                                                   # 44
    cc("assistant", 149, text("Move the drive, then tell me when it's done.")),     # 45 - claim (not a claim)
    cc("user", 150, "ok, next"),                                                   # 46
    cc("assistant", 151, text("It works now.")),                                   # 47 + unverified_claim
    cc("user", 152, "ok, next"),                                                   # 48
    {"type": "system", "subtype": "compact_boundary", "uuid": "cb1", "isSidechain": False,
     "sessionId": SID, "timestamp": ts(153), "cwd": CWD},                            # 49 compaction
    cc("assistant", 2, call("t2", "Bash", {"command": "pytest"})),                  # 50 - replay of 4 (same uuid)
    cc("user", 2.1, result("t2", "Exit code 1\nFAILED test_x", True)),              # 51 - replay of 5
    cc("user", 96, "no, that's wrong: test_x still fails"),                         # 52 - replay of 22
    cc("assistant", 160, call("b1", "Bash", {"command": "make"})),                  # 53
    cc("user", 160.1, result("b1", "Exit code 2\nmake: *** [all] Error 2", True)),  # 54
    cc("assistant", 161, call("e1", "Edit", {"file_path": "/tmp/fx/alpha/b.py", "old_string": "c", "new_string": "d"})),  # 55
    cc("user", 161.1, result("e1", "ok")),                                          # 56
    cc("assistant", 162, call("b2", "Bash", {"command": "make"})),                  # 57 - repeated (edit between)
    cc("user", 162.1, result("b2", "Exit code 2\nmake: *** [all] Error 2", True)),  # 58
    cc("assistant", 163, call("g1", "Bash", {"command": "git diff --no-index --check /dev/null x.md"})),  # 59
    cc("user", 163.1, result("g1", "Exit code 1", True)),                           # 60
    cc("assistant", 164, call("g2", "Bash", {"command": "git diff --no-index --check /dev/null x.md"})),  # 61 - repeated (diff exit 1)
    cc("user", 164.1, result("g2", "Exit code 1", True)),                           # 62
    cc("assistant", 165, call("k1", "Bash", {"command": "npm run serve"})),         # 63
    cc("user", 165.1, result("k1", "Exit code 130", True)),                         # 64
    cc("assistant", 166, call("k2", "Bash", {"command": "npm run serve"})),         # 65 - repeated (exit 130)
    cc("user", 166.1, result("k2", "Exit code 130", True)),                         # 66
    cc("assistant", 167, text("Captured -> sources/2026-09-22-x.md, pushed. Done.")),  # 67 - claim (file path)
    cc("user", 168, "ok, next"),                                                   # 68
    cc("assistant", 169, text("Wound up: main @ 47d9ba4. All complete.")),          # 69 - claim (commit after @)
    cc("user", 170, "ok, next"),                                                   # 70
    cc("assistant", 171, text("I still need the link to complete the entry.")),     # 71 - claim (not a claim)
    cc("user", 172, "ok, next"),                                                   # 72
]

# A headless permission probe: its "DONE" is not a handback to judge.
PROBE = "33333333-3333-4333-8333-333333333333"
probe = [
    {**cc("user", 0, "Call the Bash tool exactly once with this exact command: ls"), "sessionId": PROBE},  # 1
    {**cc("assistant", 1, text("DONE")), "sessionId": PROBE},                       # 2 - claim (probe session)
]

# A subagent transcript of the same session: sidechain turns never count as human.
sub = [
    {**cc("user", 30.5, "Review the parser."), "isSidechain": True, "agentId": "a1"},  # 1 - human
    {**cc("assistant", 31, text("Looks correct.")), "isSidechain": True, "agentId": "a1"},  # 2 - claim (sidechain)
]

CX = "22222222-2222-4222-8222-222222222222"
CXCWD = "/tmp/fx/beta"


def cx(minutes: float, kind: str, payload: dict) -> dict:
    return {"timestamp": ts(minutes), "type": kind, "payload": payload}


def item(minutes: float, it: dict) -> dict:
    return cx(minutes, "event_msg", {"type": "item_completed", "thread_id": CX, "turn_id": "turn1", "item": it})


def cmd(i: str, command: str, exit_code: int, parsed: list) -> dict:
    return {"type": "CommandExecution", "id": i, "command": ["/bin/zsh", "-lc", command], "cwd": f"file://{CXCWD}",
            "parsed_cmd": parsed, "status": "completed" if exit_code == 0 else "failed", "exit_code": exit_code,
            "aggregated_output": "" if exit_code == 0 else "make: *** [test] Error 2",
            "duration": {"secs": 1, "nanos": 500_000_000}}


def patch(i: str, path: str, kind: str) -> dict:
    return {"type": "FileChange", "id": i, "changes": {path: {"type": kind, "unified_diff": "@@"}}, "status": "completed"}


def msg(i: str, kind: str, s: str, phase: str | None = None) -> dict:
    it = {"type": kind, "id": i, "content": [{"type": "text" if kind == "UserMessage" else "Text", "text": s}]}
    if phase:
        it["phase"] = phase
    return it


codex = [
    cx(0, "session_meta", {"id": CX, "cwd": CXCWD, "originator": "Codex Desktop", "source": "vscode",
                           "thread_source": "user", "cli_version": "0.153.4", "git": {"branch": "main"}}),  # 1
    cx(0, "turn_context", {"turn_id": "turn1", "model": "gpt-test", "approval_policy": "never"}),            # 2
    item(1, msg("m1", "UserMessage", "Update the config and main module.")),                                # 3
    item(2, cmd("c1", "cat config.toml", 0, [{"type": "read", "cmd": "cat config.toml", "name": "config.toml", "path": "config.toml"}])),  # 4
    item(3, patch("p1", f"{CXCWD}/config.toml", "update")),                                                 # 5 - edit (read at 4)
    item(4, patch("p2", f"{CXCWD}/main.py", "update")),                                                     # 6 + edit_without_read
    item(5, patch("p3", f"{CXCWD}/new.py", "add")),                                                         # 7 - edit (new file)
    item(6, cmd("c2", "make test", 2, [{"type": "unknown", "cmd": "make test"}])),                         # 8
    item(7, cmd("c3", "make test", 2, [{"type": "unknown", "cmd": "make test"}])),                         # 9 + repeated_failure
    item(8, {"type": "Reasoning", "id": "r1", "summary_text": [], "raw_content": []}),                       # 10
    item(9, msg("a1", "AgentMessage", "Everything works now.", "final_answer")),                            # 11 + unverified_claim
    item(10, msg("m2", "UserMessage", "stop, that's not what I asked")),                                    # 12 + user_correction
    item(11, msg("a2", "AgentMessage", "Reverted; `make test` exit 0.", "final_answer")),                  # 13 - claim (pointer)
    cx(12, "event_msg", {"type": "turn_aborted", "reason": "interrupted"}),                                 # 14 aborted
]

# Project gamma: three sessions for the cross-session and loop signals (6-10).
GCWD = "/tmp/fx/gamma"
G1, G2, G3 = ("44444444-4444-4444-8444-444444444444", "55555555-5555-4555-8555-555555555555",
              "66666666-6666-4666-8666-666666666666")
LINKS = "cd /tmp/fx/gamma && python3 scripts/check_links.py --all --format=json | head -40"
RENDER = "cd /tmp/fx/gamma && python3 scripts/render_site.py --out build/site --strict --quiet"
PROBE_PY = "python3 - <<'PY'\nimport json\nfrom pathlib import Path\n" + "\n".join(
    f"print(json.loads(Path('build/site/page{i}.json').read_text())['status'])" for i in range(4)) + "\nPY"


def gc(sid: str, kind: str, minutes: float, content, **extra) -> dict:
    return {**cc(kind, minutes, content, **extra), "sessionId": sid, "cwd": GCWD, "uuid": f"{sid[:2]}-{minutes}"}


def chat(sid: str, start: float, pairs: int) -> list:
    """Ordinary work turns that fire no signal: padding to set a session's length."""
    out = []
    for i in range(pairs):
        out += [gc(sid, "assistant", start + i, text(f"Reading part {i} of the docs.")),
                gc(sid, "user", start + i + 0.5, "ok, next")]
    return out


gamma1 = [
    gc(G1, "user", 200, "Build the docs."),                                                      # 1
    gc(G1, "assistant", 201, call("x1", "Bash", {"command": LINKS})),                            # 2
    gc(G1, "user", 201.1, result("x1", "0 broken")),                                             # 3
    gc(G1, "assistant", 202, call("x2", "Bash", {"command": "npx tsc --build"})),                # 4  - recurring_failure (first session)
    gc(G1, "user", 202.1, result("x2", "error TS2304: Cannot find name 'foo' at /tmp/fx/gamma/src/a.ts:12", True)),  # 5
    gc(G1, "assistant", 203, call("x3", "Bash", {"command": RENDER})),                           # 6  - recurring_action (2 sessions only)
    gc(G1, "user", 203.1, result("x3", "built")),                                                # 7
] + chat(G1, 210, 28)                                                                            # 8-63: 63 turns
gamma2 = [
    gc(G2, "user", 300, "Fix the build."),                                                       # 1
    gc(G2, "assistant", 301, call("y1", "Bash", {"command": LINKS})),                            # 2
    gc(G2, "user", 301.1, result("y1", "0 broken")),                                             # 3
    gc(G2, "assistant", 302, call("y2", "Bash", {"command": "npx tsc --build"})),                # 4  + recurring_failure (seen in gamma1)
    gc(G2, "user", 302.1, result("y2", "error TS2304: Cannot find name 'foo' at /tmp/fx/gamma/src/b.ts:40", True)),  # 5
    gc(G2, "assistant", 303, call("y3", "Bash", {"command": RENDER})),                           # 6
    gc(G2, "user", 303.1, result("y3", "built")),                                                # 7
] + chat(G2, 310, 28)                                                                            # 8-63: 63 turns
FAIL = "Exit code 1\nFAILED tests/test_api.py::test_get - assert 404 == 200"
gamma3 = [
    gc(G3, "user", 400, "Make the API tests pass."),                                             # 1
    gc(G3, "assistant", 401, call("z1", "Bash", {"command": LINKS})),                            # 2  + recurring_action (third session)
    gc(G3, "user", 401.1, result("z1", "0 broken")),                                             # 3
]
for i in range(4):  # 4 identical failing checks with an edit to one file between: a streak
    m = 402 + i
    gamma3 += [
        gc(G3, "assistant", m, call(f"z{i}t", "Bash", {"command": "pytest tests/test_api.py"})),  # 4, 8, 12, 16 (+ no_progress_streak at 4)
        gc(G3, "user", m + 0.1, result(f"z{i}t", FAIL, True)),
        gc(G3, "assistant", m + 0.2, call(f"z{i}e", "Edit", {"file_path": f"{GCWD}/src/api.py", "old_string": f"v{i}", "new_string": f"v{i + 1}"})),
        gc(G3, "user", m + 0.3, result(f"z{i}e", "ok")),
    ]
for i in range(5):  # 5 identical long successful calls: an argument loop
    m = 410 + i
    gamma3 += [
        gc(G3, "assistant", m, call(f"z{i}p", "Bash", {"command": PROBE_PY})),                   # 20 (+ repeated_args at 20)
        gc(G3, "user", m + 0.1, result(f"z{i}p", "ok\nok\nok\nok")),
    ]
gamma3 += [
    gc(G3, "assistant", 420, call("z9p", "Bash", {"command": PROBE_PY + "\n"})),                 # 30 - repeated_args (other input)
    gc(G3, "user", 420.1, result("z9p", "ok")),
]
for i, (c1, err) in enumerate((("npm run lint", "lint: 3 problems"), ("npm run typecheck", "tsc: 2 errors"))):
    m = 422 + 2 * i  # the same failing command twice with no edit between: two more loop rows
    gamma3 += [
        gc(G3, "assistant", m, call(f"zl{i}a", "Bash", {"command": c1})),
        gc(G3, "user", m + 0.1, result(f"zl{i}a", f"Exit code 1\n{err}", True)),
        gc(G3, "assistant", m + 0.5, call(f"zl{i}b", "Bash", {"command": c1})),                   # 34, 38 + repeated_failure
        gc(G3, "user", m + 0.6, result(f"zl{i}b", f"Exit code 1\n{err}", True)),
    ]
gamma3 += chat(G3, 430, 85) + [
    gc(G3, "assistant", 600, text("Still failing: test_get returns 404; I stopped to ask.")),     # last - claim
]
# gamma3: 3 loop rows and its length against a median of 63 over gamma's 50+-turn sessions:
# + loop_cost_outlier, cited at the streak (line 4). With only the streak (1 loop row) it would not fire.


def write(path: Path, recs: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


if __name__ == "__main__":
    write(ROOT / "claude" / "-tmp-fx-alpha" / f"{SID}.jsonl", claude)
    write(ROOT / "claude" / "-tmp-fx-alpha" / SID / "subagents" / "agent-a1.jsonl", sub)
    write(ROOT / "claude" / "-tmp-fx-probe" / f"{PROBE}.jsonl", probe)
    for sid, recs in ((G1, gamma1), (G2, gamma2), (G3, gamma3)):
        write(ROOT / "claude" / "-tmp-fx-gamma" / f"{sid}.jsonl", recs)
    write(ROOT / "codex" / "2026" / "01" / "05" / f"rollout-2026-01-05T10-00-00-{CX}.jsonl", codex)
