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


def write(path: Path, recs: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


if __name__ == "__main__":
    write(ROOT / "claude" / "-tmp-fx-alpha" / f"{SID}.jsonl", claude)
    write(ROOT / "claude" / "-tmp-fx-alpha" / SID / "subagents" / "agent-a1.jsonl", sub)
    write(ROOT / "codex" / "2026" / "01" / "05" / f"rollout-2026-01-05T10-00-00-{CX}.jsonl", codex)
