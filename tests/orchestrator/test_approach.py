"""Tests for sorting agent tool calls into phases (orchestrator/approach)."""

from __future__ import annotations

import json

import pytest
from backend.orchestrator import approach
from backend.orchestrator.approach import classify_call, classify_shell


@pytest.mark.parametrize(
    ("line", "phase"),
    [
        ("rg -n 'separable' src/", "explore"),
        ("sed -n '1,80p' app/main.py", "explore"),
        ("ls -la && cat README.md", "explore"),
        ("pytest -q tests/", "verify"),
        ("cd app && uv run pytest -x", "verify"),
        ("python -m pytest", "verify"),
        ("timeout 120 npm test", "verify"),
        ("npm run typecheck", "verify"),
        ("cargo clippy --all", "verify"),
        ("ruff check . && rg TODO", "verify"),
        ("sed -i 's/a/b/' x.py", "edit"),
        ("cat > notes.md <<'EOF'\nhello\nEOF", "edit"),
        ("mkdir -p src/new", "edit"),
        ("npm install", "setup"),
        ("uv add httpx", "setup"),
        ("pip install -e .", "setup"),
        ("git diff --stat", "git"),
        ("curl -s https://example.com", "web"),
        ("python script.py", "verify"),  # running the program to check it
        ("", "other"),
    ],
)
def test_shell_commands_get_the_phase_of_what_they_do(line: str, phase: str) -> None:
    assert classify_shell(line).phase == phase


@pytest.mark.parametrize(
    "line",
    [
        "rg 'a->b' src/",  # an arrow, not a redirect
        'python -c "print(1 > 0)"',  # a comparison inside quotes
        "rg foo 2>&1 | head",  # stderr merged, nothing written
        "grep -r foo . > /dev/null",  # output thrown away
    ],
)
def test_a_greater_than_sign_that_writes_nothing_is_not_an_edit(line: str) -> None:
    assert classify_shell(line).phase != "edit"
    assert classify_shell(line).edited == ()


def test_a_redirect_records_the_file_written() -> None:
    assert classify_shell("echo x >> CHANGELOG.md").edited == ("CHANGELOG.md",)


def test_cat_and_sed_record_the_files_read() -> None:
    result = classify_shell("cat src/a.py && sed -n '1,9p' src/b.py | head")

    assert result.read == ("src/a.py", "src/b.py")


def test_an_unbalanced_quote_still_classifies() -> None:
    assert classify_shell("rg 'unterminated src/").phase == "explore"


@pytest.mark.parametrize(
    ("tool", "tool_input", "phase"),
    [
        ("Grep", {"pattern": "foo"}, "explore"),
        ("Read", {"file_path": "/w/a.py"}, "explore"),
        ("Edit", {"file_path": "/w/a.py"}, "edit"),
        ("Write", {"file_path": "/w/new.py"}, "edit"),
        ("WebFetch", {"url": "https://x"}, "web"),
        ("Task", {"description": "look around"}, "delegate"),
        ("Bash", {"command": "pytest"}, "verify"),
        ("Bash", None, "other"),
        ("TodoWrite", {}, "other"),
        ("mcp__fs__read_text", {}, "explore"),
        ("shell", {"command": ["bash", "-lc", "rg foo"]}, "explore"),
        ("shell", {"command": ["pytest", "-q"]}, "verify"),
    ],
)
def test_tools_are_classified_across_agents(tool: str, tool_input: object, phase: str) -> None:
    assert classify_call(tool, tool_input).phase == phase


def test_claude_edits_and_reads_record_their_file() -> None:
    assert classify_call("Edit", {"file_path": "/w/a.py"}).edited == ("/w/a.py",)
    assert classify_call("Read", {"file_path": "/w/b.py"}).read == ("/w/b.py",)


def test_apply_patch_records_every_file_it_touches() -> None:
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n*** Add File: b.py\n*** End Patch"

    assert classify_call("apply_patch", patch).edited == ("a.py", "b.py")
    assert classify_call("exec_command", {"cmd": f"apply_patch <<'P'\n{patch}\nP"}).edited == (
        "a.py",
        "b.py",
    )


def _event(name: str, ts: str | None, **attributes: object) -> dict[str, object]:
    return {"name": name, "ts": ts, "received_at": "2026-10-22T10:09:00Z", "attributes": attributes}


def _call(ts: str, tool: str, ok: str = "true", **tool_input: object) -> dict[str, object]:
    return _event(
        "claude_code.tool_result",
        ts,
        tool_name=tool,
        success=ok,
        duration_ms="100",
        tool_input=json.dumps(tool_input),
    )


def test_stats_describe_how_the_task_was_approached() -> None:
    events = [
        _event("claude_code.user_prompt", "2026-10-22T10:00:00Z"),
        _call("2026-10-22T10:00:02Z", "Grep", pattern="verb"),
        _call("2026-10-22T10:00:04Z", "Read", file_path="a.py"),
        _call("2026-10-22T10:00:10Z", "Edit", file_path="a.py"),
        _call("2026-10-22T10:00:20Z", "Bash", "false", command="pytest"),
        _call("2026-10-22T10:00:30Z", "Edit", file_path="a.py"),
        _call("2026-10-22T10:00:40Z", "Bash", command="pytest"),
        _call("2026-10-22T10:00:45Z", "Bash", command="pytest"),  # no edit since: not a cycle
    ]

    result = approach.approach_from_events(events)
    stats = result.stats

    assert stats.calls == 7
    assert stats.failures == 1
    assert stats.calls_before_first_edit == 2
    assert stats.seconds_to_first_edit == 10.0
    assert stats.seconds_to_first_verify == 20.0
    assert stats.edit_verify_cycles == 2
    assert stats.files_read == ["a.py"]
    assert stats.files_edited == ["a.py"]
    assert stats.span_s == 45.0
    assert stats.phase_calls["verify"] == 3
    assert stats.phase_ms["explore"] == 200
    assert set(stats.phase_calls) == set(approach.PHASES)
    assert [s.index for s in result.steps] == list(range(7))


def test_a_job_that_never_edits_has_no_first_edit() -> None:
    stats = approach.approach_from_events([_call("2026-10-22T10:00:02Z", "Grep")]).stats

    assert stats.calls_before_first_edit is None
    assert stats.seconds_to_first_edit is None
    assert stats.edit_verify_cycles == 0


def test_steps_without_times_still_appear_in_order() -> None:
    events = [_event("claude_code.tool_result", None, tool_name="Read")]
    events[0]["received_at"] = None

    (step,) = approach.approach_from_events(events).steps

    assert step.offset_s is None
    assert step.phase == "explore"


# --- shapes seen in real claude and codex runs ---------------------------------


def test_a_heredoc_body_is_data_not_commands() -> None:
    # HTML is full of `>`, and a body line could start with `rm` or `git`.
    line = "\n".join(
        [
            "cat > public/index.html << 'HTMLEOF'",
            "<!DOCTYPE html>",
            '<div class="a">',
            "rm -rf x",
            "</div>",
            "HTMLEOF",
        ]
    )

    result = classify_shell(line)

    assert result.phase == "edit"
    assert result.edited == ("public/index.html",)


def test_a_heredoc_cut_off_before_its_delimiter_is_still_dropped() -> None:
    assert classify_shell("cat > app.js << 'JSEOF'\nconst a = b > c;\ngit push").edited == (
        "app.js",
    )


@pytest.mark.parametrize(
    "line",
    [
        "bash tests/test_german_practice.sh",
        "bash -n german-terminal-practice.sh",
        "printf '4\\n' | bash german-terminal-practice.sh | sed -n '1,30p'",
        "python3 app.py --check",
        "curl -s http://127.0.0.1:8731/ | head",
    ],
)
def test_running_the_project_or_its_tests_is_verify(line: str) -> None:
    assert classify_shell(line).phase == "verify"


@pytest.mark.parametrize("line", ['python3 -c "print(1)"', "node -e 'x()'", "bash"])
def test_inline_code_is_not_verify(line: str) -> None:
    assert classify_shell(line).phase == "other"


def test_a_log_written_to_tmp_is_not_an_edit() -> None:
    result = classify_shell("python3 -m http.server 8731 >/tmp/server.log 2>&1 &")

    assert result.phase != "edit"
    assert result.edited == ()


def test_write_file_names_its_file_under_file() -> None:
    assert classify_call("write_file", {"file": "public/index.html"}).edited == (
        "public/index.html",
    )


def test_codex_code_mode_wrappers_are_not_counted_twice() -> None:
    events = [
        _event(
            "codex.tool_result",
            "2026-10-22T10:00:01Z",
            tool_name="exec_command",
            arguments='{"cmd": "rg foo"}',
        ),
        _event(
            "codex.tool_result",
            "2026-10-22T10:00:01Z",
            tool_name="exec",
            arguments='const r = await tools.exec_command({cmd:"rg foo"}); text(r)',
        ),
    ]

    (step,) = approach.approach_from_events(events).steps

    assert step.tool == "exec_command"


def test_an_input_cut_off_at_the_attribute_limit_is_still_read() -> None:
    # codex truncates attributes at 4096 characters, mid-string.
    raw = '{"cmd":"cat \\u003e public/app.js \\u003c\\u003c \'JSEOF\'\\nlet a = 1;\\nfunction rend'
    events = [
        _event("codex.tool_result", "2026-10-22T10:00:01Z", tool_name="exec_command", arguments=raw)
    ]

    (step,) = approach.approach_from_events(events).steps

    assert step.phase == "edit"
    assert step.target is not None
    assert step.target.startswith("cat > public/app.js")


def test_paths_are_shown_relative_to_the_repo() -> None:
    repo = "/home/u/.agentshowdown/clones/owner-app"
    events = [
        _call("2026-10-22T10:00:01Z", "Read", file_path=f"{repo}/src/a.py"),
        _call("2026-10-22T10:00:02Z", "Write", file_path=f"{repo}/ui/index.html"),
    ]

    result = approach.approach_from_events(events, workdir=repo)

    assert result.stats.files_read == ["src/a.py"]
    assert result.stats.files_edited == ["ui/index.html"]
    assert result.steps[0].target == "src/a.py"


def test_writing_the_status_file_is_reporting_not_editing() -> None:
    events = [
        _call("2026-10-22T10:00:01Z", "Edit", file_path="src/a.py"),
        _call("2026-10-22T10:00:02Z", "Write", file_path="/repo/.agent_status.json"),
    ]

    result = approach.approach_from_events(
        events, workdir="/repo", protocol_files=[".agent_status.json"]
    )

    assert [s.phase for s in result.steps] == ["edit", "other"]
    assert result.stats.files_edited == ["src/a.py"]
