"""How an agent went about a task: its tool calls sorted into phases.

Raw tool names don't compare across agents. claude calls `Grep`, `Read`
and `Edit`; codex does nearly everything through one shell tool
(`exec_command` / `shell`) plus `apply_patch`. Counting tool names would
show codex as "one tool, 200 calls". So every call is classified by what
it *did* -- explored the code, edited it, verified it, set up the
environment, used git or the web, or handed work to a subagent -- reading
shell commands to tell `rg foo` from `pytest` from `sed -i`.

The classification is a heuristic, meant for roughly judging two runs
side by side, not for auditing. Anything it can't place is "other", never
guessed into a phase.

Everything here is stdlib-only and side-effect free.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

Phase = Literal["explore", "edit", "verify", "setup", "git", "web", "delegate", "other"]

# Display order, roughly the order work tends to happen in.
PHASES: tuple[Phase, ...] = (
    "explore",
    "edit",
    "verify",
    "setup",
    "git",
    "web",
    "delegate",
    "other",
)

# When one shell command does several things (`cd x && rg y && pytest`),
# the phase that says most about intent wins: running the tests is the
# point of that line, the `rg` is incidental.
_PRIORITY: tuple[Phase, ...] = ("verify", "edit", "setup", "git", "web", "delegate", "explore")

# Most files listed per job; a job that reads thousands of files is told
# apart from one that reads ten well before this.
_MAX_FILES = 300

# Longest target line kept per step; the full input is in the raw event.
_MAX_TARGET_CHARS = 200

# `["bash", "-lc", "<line>"]`: shell, flag, command line.
_WRAPPED_ARGV_LEN = 3

# Structured (non-shell) tools, by lowercased name.
_TOOL_PHASES: dict[str, Phase] = {
    # claude
    "read": "explore",
    "grep": "explore",
    "glob": "explore",
    "ls": "explore",
    "notebookread": "explore",
    "edit": "edit",
    "multiedit": "edit",
    "write": "edit",
    "notebookedit": "edit",
    "webfetch": "web",
    "websearch": "web",
    "task": "delegate",
    "agent": "delegate",
    # Planning: named so the name-based fallback below doesn't read
    # "TodoWrite" as an edit.
    "todowrite": "other",
    "todoread": "other",
    "exitplanmode": "other",
    "update_plan": "other",
    # codex
    "apply_patch": "edit",
    "view_image": "explore",
    "web_search": "web",
    "read_file": "explore",
    "list_dir": "explore",
    "grep_files": "explore",
}

_SHELL_TOOLS = frozenset(
    {"bash", "shell", "exec_command", "local_shell", "container.exec", "unified_exec"}
)

_EXPLORE_COMMANDS = frozenset(
    {
        "rg", "grep", "egrep", "fgrep", "ag", "ack", "find", "fd", "ls", "tree", "cat",
        "head", "tail", "less", "more", "awk", "wc", "nl", "file", "stat", "pwd", "du",
        "diff", "jq", "bat", "sed", "realpath", "dirname", "basename", "which", "type",
        "echo", "printf", "sort", "uniq", "cut", "xxd", "od", "strings",
    }
)  # fmt: skip
_EDIT_COMMANDS = frozenset(
    {"apply_patch", "applypatch", "patch", "tee", "touch", "mkdir", "mv", "cp", "rm", "ln"}
)
_VERIFY_COMMANDS = frozenset(
    {
        "pytest", "py.test", "tox", "nox", "ruff", "mypy", "ty", "pyright", "flake8",
        "pylint", "black", "isort", "eslint", "oxlint", "biome", "prettier", "tsc", "jest",
        "vitest", "mocha", "playwright", "cypress", "shellcheck", "hadolint", "rspec",
        "phpunit", "ctest", "pre-commit",
    }
)  # fmt: skip
_SETUP_COMMANDS = frozenset({"pip", "pip3", "apt", "apt-get", "brew", "conda", "poetry", "pipx"})
_GIT_COMMANDS = frozenset({"git", "gh"})
_WEB_COMMANDS = frozenset({"curl", "wget", "http", "httpie"})

# Package-manager subcommands that verify (`npm test`) or set up
# (`npm install`). `run <script>` is judged by the script's name.
_PM_COMMANDS = frozenset({"npm", "pnpm", "yarn", "bun", "cargo", "go", "make", "just", "dotnet"})
_PM_VERIFY = frozenset(
    {"test", "lint", "check", "clippy", "vet", "typecheck", "build", "fmt", "format", "tsc"}
)
_PM_SETUP = frozenset({"install", "i", "ci", "add", "sync", "get", "mod", "fetch", "restore"})

# Wrappers that run the command after them: `uv run pytest`, `npx tsc`,
# `timeout 60 cargo test`, `python -m pytest`.
_WRAPPERS = frozenset({"sudo", "env", "time", "timeout", "nice", "npx", "bunx", "exec", "command"})

# Shell control operators that separate commands on one line.
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "\n"})

_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.MULTILINE)
# A write redirection (`> f`, `>> f`), but not `2>&1`, `> /dev/null` or a
# scratch file under /tmp (a server log, say), which changes no code.
_REDIRECT = re.compile(r"(?<![0-9&=-])>>?\s*(?!&|/dev/|/tmp/)([^\s;&|]+)")
# Quoted strings, blanked out before looking for redirects, so the `>` in
# `rg "a > b"` or `python -c "print(1 > 0)"` isn't read as a write.
_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
# Interpreters that run a script file named after them. An agent running
# the project's own script -- its test script, or the program to see that it
# works -- is checking its work, so that counts as verify. Inline code
# (`python -c`, `node -e`) says nothing and stays unclassified.
_INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "python", "python3", "node", "deno", "ruby", "perl", "php"}
)
_SCRIPT_FILE = re.compile(r"\.(?:sh|bash|py|js|mjs|cjs|ts|rb|pl|php)$")

# A tool input as codex sends it once its 4096-character attribute limit
# cuts it off: no longer valid JSON, but the leading field still readable.
_TRUNCATED_FIELD = re.compile(
    r'^\s*\{\s*"(cmd|command|file_path|path|file)"\s*:\s*"((?:[^"\\]|\\.)*)'
)

# codex's code mode logs each call twice: the real tool (`exec_command`,
# `apply_patch`) and an `exec` wrapper whose input is the script that
# invoked it, `await tools.exec_command({...})`. Only the real one counts.
_CODE_MODE_CALL = re.compile(r"\btools\.\w+\s*\(")

# A heredoc's body, up to its delimiter line (or the end, when the input
# was cut off). The body is data -- HTML full of `>`, lines that start with
# `rm` -- so it is dropped before the command line is read; the line that
# opens it (`cat > page.html <<'EOF'`) is kept.
_HEREDOC = re.compile(
    r"(<<-?\s*(['\"]?)(\w+)\2[^\n]*)\n.*?(?:^\s*\3\s*$|\Z)", re.DOTALL | re.MULTILINE
)

# A request to the agent's own machine is checking the app it just
# started, not using the web.
_LOCAL_URL = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)\b")

# Commands whose path arguments are files being read.
_READERS = frozenset({"cat", "head", "tail", "nl", "less", "more", "bat", "sed"})


@dataclass(frozen=True)
class Classified:
    """What one tool call did.

    Attributes:
        phase: Which phase of the work it belongs to.
        read: Files it read, when that can be told.
        edited: Files it changed, when that can be told.
    """

    phase: Phase
    read: tuple[str, ...] = ()
    edited: tuple[str, ...] = ()


def _salvage(raw: str) -> dict[str, str] | None:
    """The leading field of a JSON input cut off mid-string, or None."""
    match = _TRUNCATED_FIELD.match(raw)
    if match is None:
        return None
    body = match.group(2)
    # Drop a dangling half escape (`\` or `\u00`) left by the cut.
    body = re.sub(r"\\(?:u[0-9a-fA-F]{0,3})?$", "", body)
    try:
        value = json.loads(f'"{body}"')
    except ValueError:
        return None
    return {match.group(1): value}


def _parse_input(attributes: Mapping[str, object]) -> object:
    """The tool call's input: a dict when it was JSON, else the raw text."""
    for key in ("tool_input", "tool_parameters", "arguments", "input"):
        raw = attributes.get(key)
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except ValueError:
                return _salvage(raw) or raw
        if raw is not None:
            return raw
    return None


def is_wrapper_event(attributes: Mapping[str, object]) -> bool:
    """Whether a stored `tool_result` event only wraps another call that is
    logged on its own, so counting it would count that call twice."""
    tool = str(attributes.get("tool_name") or attributes.get("tool") or "")
    return is_wrapper_call(tool, _parse_input(attributes))


def is_wrapper_call(tool: str, tool_input: object) -> bool:
    """Whether a call only wraps another one that is logged on its own."""
    return (
        tool.lower() == "exec"
        and isinstance(tool_input, str)
        and _CODE_MODE_CALL.search(tool_input) is not None
    )


def _command_text(tool_input: object) -> str | None:
    """The shell command line in a tool input, as one string."""
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return None
    for key in ("command", "cmd", "full_command", "bash_command"):
        value = tool_input.get(key)
        if isinstance(value, list):
            parts = [str(p) for p in value]
            # codex wraps commands as ["bash", "-lc", "<line>"].
            if (
                len(parts) >= _WRAPPED_ARGV_LEN
                and parts[0].endswith("sh")
                and parts[1].startswith("-")
            ):
                return parts[-1]
            return shlex.join(parts)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _patch_files(tool_input: object) -> tuple[str, ...]:
    """Files named in an apply_patch body, wherever in the input it sits."""
    texts: list[str] = []
    if isinstance(tool_input, str):
        texts.append(tool_input)
    elif isinstance(tool_input, dict):
        texts.extend(v for v in tool_input.values() if isinstance(v, str))
        texts.extend(
            p for v in tool_input.values() if isinstance(v, list) for p in v if isinstance(p, str)
        )
    return tuple(f.strip() for text in texts for f in _PATCH_FILE.findall(text))


def _tokens(line: str) -> list[str]:
    """Splits a command line into words and control operators."""
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        # An unbalanced quote: a rough split still finds the command names.
        return re.split(r"\s+|(?=[;&|])|(?<=[;&|])", line)


def _segments(line: str) -> list[list[str]]:
    """The separate commands on one line, each as its words."""
    segments: list[list[str]] = [[]]
    tokens = [t for physical in line.splitlines() for t in [*_tokens(physical), "\n"]]
    for token in tokens:
        if token in _SEPARATORS or (token and set(token) <= set(";&|")):
            segments.append([])
        elif token:
            segments[-1].append(token)
    return [s for s in segments if s]


def _strip_wrappers(words: list[str]) -> list[str]:
    """Drops env assignments and wrapper commands to reach the real one."""
    i = 0
    while i < len(words):
        word = words[i]
        name = word.rsplit("/", 1)[-1]
        if _ENV_ASSIGNMENT.match(word):
            i += 1
        elif name in _WRAPPERS:
            i += 1
            # `timeout 60 ...`: skip the wrapper's numeric/flag arguments.
            while i < len(words) and (words[i].startswith("-") or words[i][:1].isdigit()):
                i += 1
        elif name in ("uv", "poetry", "pipenv", "hatch", "pdm") and words[i + 1 : i + 2] == ["run"]:
            i += 2
            while i < len(words) and words[i].startswith("-"):
                i += 1
        elif re.fullmatch(r"python[0-9.]*", name) and words[i + 1 : i + 2] == ["-m"]:
            i += 2
        else:
            break
    return words[i:]


def _pm_phase(words: list[str]) -> Phase | None:
    """A package manager's phase from its subcommand, or None."""
    rest = [w for w in words[1:] if not w.startswith("-")]
    if not rest:
        return None
    sub = rest[0]
    if sub == "run" and len(rest) > 1:
        sub = rest[1]
    if any(part in _PM_VERIFY for part in re.split(r"[:_-]", sub)):
        return "verify"
    if sub in _PM_SETUP:
        return "setup"
    return None


# Commands whose name alone gives their phase, checked in this order.
_NAME_PHASES: tuple[tuple[frozenset[str], Phase], ...] = (
    (_VERIFY_COMMANDS | {"unittest"}, "verify"),
    (_EDIT_COMMANDS, "edit"),
    (_SETUP_COMMANDS, "setup"),
    (_GIT_COMMANDS, "git"),
    (_WEB_COMMANDS, "web"),
    (_EXPLORE_COMMANDS, "explore"),
)

# Shell built-ins that only set up the next command.
_NEUTRAL_COMMANDS = frozenset({"cd", "pushd", "popd", "source", ".", "set", "export", "true", ":"})


def _script_phase(words: list[str]) -> Phase | None:
    """`bash tests/run.sh`, `bash -n x.sh`, `python app.py`: verify; None
    for inline code or a bare interpreter."""
    args = words[1:]
    if words[0].rsplit("/", 1)[-1] in ("bash", "sh", "zsh") and "-n" in args:
        return "verify"  # a syntax check
    files = [a for a in args if not a.startswith("-")]
    if files and _SCRIPT_FILE.search(files[0]):
        return "verify"
    return None


def _in_place_edit(words: list[str]) -> Phase | None:
    """`sed -i` / `perl -pi` edit a file; other uses read or run."""
    if any(w.startswith("-i") or w == "-pi" for w in words[1:]):
        return "edit"
    return _script_phase(words) if words[0].endswith("perl") else None


def _uv_phase(words: list[str]) -> Phase | None:
    return "setup" if words[1:2] and words[1] in ("add", "sync", "lock", "pip") else None


def _web_phase(words: list[str]) -> Phase | None:
    return "verify" if any(_LOCAL_URL.search(w) for w in words[1:]) else "web"


def _special_phase(name: str, words: list[str]) -> Phase | None:
    """Phases that depend on a subcommand, flag or argument, not just the
    name; None when the command needs no special reading."""
    if name in ("sed", "perl"):
        return _in_place_edit(words)
    handlers = (
        (_INTERPRETERS, _script_phase),
        (_PM_COMMANDS, _pm_phase),
        (frozenset({"uv"}), _uv_phase),
        (_WEB_COMMANDS, _web_phase),
    )
    return next((handler(words) for names, handler in handlers if name in names), None)


# Commands `_special_phase` reads beyond their name.
_SPECIAL_COMMANDS = _INTERPRETERS | _PM_COMMANDS | _WEB_COMMANDS | {"uv", "sed"}


def _segment_phase(words: list[str]) -> Phase | None:
    """The phase of one command, or None when it says nothing."""
    words = _strip_wrappers(words)
    if not words:
        return None
    name = words[0].rsplit("/", 1)[-1]
    if name in _NEUTRAL_COMMANDS:
        return None
    if name in _SPECIAL_COMMANDS:
        special = _special_phase(name, words)
        if special is not None or name not in _EXPLORE_COMMANDS:
            return special
    return next((phase for names, phase in _NAME_PHASES if name in names), None)


def _read_paths(words: list[str]) -> list[str]:
    """Path arguments of a file-reading command like `cat` or `sed -n`."""
    words = _strip_wrappers(words)
    if not words or words[0].rsplit("/", 1)[-1] not in _READERS:
        return []
    args = [w for w in words[1:] if not w.startswith("-")]
    if words[0].endswith("sed") and args:
        args = args[1:]  # the sed script
    return [a for a in args if "/" in a or "." in a]


def classify_shell(line: str) -> Classified:
    """Classifies one shell command line.

    Args:
        line: The command as the agent ran it, e.g. "cd app && pytest -q".

    Returns:
        The most telling phase among its commands (see `_PRIORITY`), with
        files read by `cat`-like commands and files written by redirects.
    """
    patch = _patch_files(line) if "apply_patch" in line or "*** Begin Patch" in line else None
    line = _HEREDOC.sub(r"\1", line)
    segments = _segments(line)
    phases = {p for p in (_segment_phase(s) for s in segments) if p is not None}
    edited = tuple(_REDIRECT.findall(_QUOTED.sub("''", line)))
    if edited:
        phases.add("edit")
    if patch is not None:
        phases.add("edit")
        edited += patch
    phase = next((p for p in _PRIORITY if p in phases), "other")
    read = tuple(path for s in segments for path in _read_paths(s))
    return Classified(phase, read=read, edited=edited)


def classify_call(tool: str, tool_input: object) -> Classified:
    """Classifies one tool call by what it did.

    Args:
        tool: The tool's name as the agent reported it, e.g. "Grep" or
            "exec_command".
        tool_input: Its parsed input: a dict for JSON, the raw text
            otherwise, or None when the agent sent no details.

    Returns:
        The call's phase and any files it read or edited.
    """
    name = tool.lower()
    if name in _SHELL_TOOLS:
        line = _command_text(tool_input)
        return classify_shell(line) if line else Classified("other")
    path = None
    if isinstance(tool_input, dict):
        value = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("file")
        path = value if isinstance(value, str) and value else None
    phase = _TOOL_PHASES.get(name)
    if phase is None:
        # An MCP or newer tool: judge by its name, else leave it as other.
        if any(w in name for w in ("read", "grep", "search", "glob", "list", "find", "view")):
            phase = "explore"
        elif any(w in name for w in ("edit", "write", "patch", "replace", "create")):
            phase = "edit"
        else:
            phase = "other"
    if name == "apply_patch":
        return Classified("edit", edited=_patch_files(tool_input))
    if phase == "edit" and path:
        return Classified(phase, edited=(path,))
    if phase == "explore" and path and name in ("read", "notebookread", "read_file"):
        return Classified(phase, read=(path,))
    return Classified(phase)


@dataclass(frozen=True)
class Step:
    """One tool call, placed on the job's clock.

    Attributes:
        index: Position among the job's tool calls, 0-based.
        offset_s: Seconds since the job's first event; None when the
            agent sent no usable time.
        phase: What the call did.
        tool: The tool's own name, e.g. "Bash".
        target: What it acted on (command, file), cut to one line.
        ok: Whether it succeeded, when reported.
        duration_ms: How long it took, when reported.
    """

    index: int
    offset_s: float | None
    phase: Phase
    tool: str
    target: str | None
    ok: bool | None
    duration_ms: int | None


@dataclass(frozen=True)
class ApproachStats:
    """Numbers that sum up how a job went about its task.

    Attributes:
        calls: Tool calls in total.
        failures: Calls that reported failure.
        phase_calls: Calls per phase; every phase present, zero or not.
        phase_ms: Reported tool time per phase, in milliseconds.
        calls_before_first_edit: How much the agent looked around before
            changing anything; None if it never edited.
        seconds_to_first_edit: Same, in time; None if it never edited or
            sent no times.
        seconds_to_first_verify: When it first ran tests, lint or a build.
        edit_verify_cycles: Times it edited and then verified. One means
            "wrote it, checked it"; many means it iterated against the tests.
        files_read: Distinct files it read, first-read order.
        files_edited: Distinct files it changed, first-edit order.
        span_s: Seconds from the first event to the last tool call.
    """

    calls: int
    failures: int
    phase_calls: dict[str, int]
    phase_ms: dict[str, int]
    calls_before_first_edit: int | None
    seconds_to_first_edit: float | None
    seconds_to_first_verify: float | None
    edit_verify_cycles: int
    files_read: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    span_s: float | None = None


@dataclass(frozen=True)
class Approach:
    """A job's tool calls as phases, plus the stats drawn from them."""

    steps: list[Step]
    stats: ApproachStats


def _parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    if isinstance(value, float):
        return int(value)
    return None


def _ok(attributes: Mapping[str, object]) -> bool | None:
    value = attributes.get("success")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _target(tool_input: object) -> str | None:
    """One line saying what the call acted on."""
    text: str | None = None
    if isinstance(tool_input, str):
        text = tool_input
    elif isinstance(tool_input, dict):
        text = _command_text(tool_input)
        if text is None:
            for key in ("file_path", "path", "file", "pattern", "url", "query", "description"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    text = value
                    break
    if not text:
        return None
    flat = " ".join(text.split())
    if len(flat) <= _MAX_TARGET_CHARS:
        return flat
    return flat[: _MAX_TARGET_CHARS - 1] + "…"


def _add_unique(into: list[str], seen: set[str], paths: Iterable[str]) -> None:
    for path in paths:
        if path not in seen and len(into) < _MAX_FILES:
            seen.add(path)
            into.append(path)


def _relative(path: str, workdir: str | None) -> str:
    """`path` relative to the repo when it lies inside it, else unchanged.

    sbx clones the repo to the same absolute path inside the sandbox, so
    the job's host-side `repo` is also the agent's working directory.
    """
    path = path.removeprefix("./")
    if workdir:
        prefix = workdir.rstrip("/") + "/"
        return path.removeprefix(prefix)
    return path


def approach_from_events(
    events: Iterable[Mapping[str, object]],
    *,
    workdir: str | None = None,
    protocol_files: Collection[str] = (),
) -> Approach:
    """Turns a job's stored OTel events into its approach.

    Args:
        events: Stored events, oldest first, each with "name", "ts",
            "received_at" and "attributes" (see `StateStore.get_agent_events`).
        workdir: The repo's path in the sandbox; file paths under it are
            shown relative to it, so two agents' paths line up.
        protocol_files: Files the orchestrator asks every agent to write
            (the status file). Writing one is reporting, not editing code:
            such a call is "other" and the file isn't listed.

    Returns:
        One step per completed tool call (`*.tool_result`), and the stats.
        A job that exported no tool calls gets no steps and zeroed stats.
    """
    protocol = {f.removeprefix("./") for f in protocol_files}
    start: dt.datetime | None = None
    steps: list[Step] = []
    read: list[str] = []
    edited: list[str] = []
    seen_read: set[str] = set()
    seen_edited: set[str] = set()
    for event in events:
        at = _parse_time(event.get("ts")) or _parse_time(event.get("received_at"))
        if start is None and at is not None:
            start = at
        name = str(event.get("name") or "")
        if not name.endswith(".tool_result"):
            continue
        attributes = event.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        tool = str(attributes.get("tool_name") or attributes.get("tool") or "unknown")
        tool_input = _parse_input(attributes)
        if is_wrapper_call(tool, tool_input):
            continue
        classified = classify_call(tool, tool_input)
        files_read = [_relative(f, workdir) for f in classified.read]
        files_edited = [_relative(f, workdir) for f in classified.edited]
        phase = classified.phase
        if files_edited and all(f in protocol for f in files_edited):
            phase, files_edited = "other", []
        _add_unique(read, seen_read, files_read)
        _add_unique(edited, seen_edited, files_edited)
        target = _target(tool_input)
        if target and workdir:
            target = target.replace(workdir.rstrip("/") + "/", "")
        offset = (at - start).total_seconds() if at is not None and start is not None else None
        steps.append(
            Step(
                index=len(steps),
                offset_s=round(offset, 3) if offset is not None else None,
                phase=phase,
                tool=tool,
                target=target,
                ok=_ok(attributes),
                duration_ms=_as_int(attributes.get("duration_ms")),
            )
        )
    return Approach(steps=steps, stats=_stats(steps, read, edited))


def _stats(steps: list[Step], read: list[str], edited: list[str]) -> ApproachStats:
    phase_calls: dict[str, int] = {p: 0 for p in PHASES}
    phase_ms: dict[str, int] = {p: 0 for p in PHASES}
    first_edit: Step | None = None
    first_verify: Step | None = None
    cycles = 0
    edited_since_verify = False
    for step in steps:
        phase_calls[step.phase] += 1
        phase_ms[step.phase] += step.duration_ms or 0
        if step.phase == "edit":
            first_edit = first_edit or step
            edited_since_verify = True
        elif step.phase == "verify":
            first_verify = first_verify or step
            if edited_since_verify:
                cycles += 1
                edited_since_verify = False
    offsets = [s.offset_s for s in steps if s.offset_s is not None]
    return ApproachStats(
        calls=len(steps),
        failures=sum(1 for s in steps if s.ok is False),
        phase_calls=phase_calls,
        phase_ms=phase_ms,
        calls_before_first_edit=first_edit.index if first_edit else None,
        seconds_to_first_edit=first_edit.offset_s if first_edit else None,
        seconds_to_first_verify=first_verify.offset_s if first_verify else None,
        edit_verify_cycles=cycles,
        files_read=read,
        files_edited=edited,
        span_s=max(offsets) if offsets else None,
    )
