"""Storing credentials in sbx's secret store.

agentshowdown never persists a secret itself. Values go to `sbx secret set`,
which either stores them in sbx's own store or -- with `--ref` / `--command`
-- stores a *reference* that sbx resolves on demand, so the value never
passes through this process at all. That is why the UI prefers those two.

Reading back is names only. `sbx secret ls` reports which services have a
secret, never the values, and neither does this module.
"""

from __future__ import annotations

import re
import shutil

from backend.logging import log
from backend.orchestrator.process import RunOptions, run

# `sbx secret ls` prints a fixed-width table with no --json option, e.g.
#   SCOPE      TYPE      NAME         SECRET
#   (global)   service   github       (stored)
_COLUMN_GAP = re.compile(r"\s{2,}")
_EXPECTED_COLUMNS = 4


def sbx_available() -> bool:
    """Returns whether the sbx CLI is on PATH."""
    return shutil.which("sbx") is not None


def parse_secret_table(stdout: str) -> list[dict[str, str]]:
    """Parses `sbx secret ls` output into rows.

    Args:
        stdout: Raw command output.

    Returns:
        One dict per stored secret. Malformed lines are skipped rather than
        raising: this is a display listing, and a future column change must
        not take the settings page down.
    """
    rows: list[dict[str, str]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("SCOPE"):
            continue
        parts = _COLUMN_GAP.split(stripped)
        if len(parts) != _EXPECTED_COLUMNS:
            continue
        scope, kind, name, state = parts
        rows.append({"scope": scope, "type": kind, "name": name, "state": state})
    return rows


def list_secrets() -> dict[str, object]:
    """Lists which services have a secret stored.

    Returns:
        `available` says whether sbx could be queried at all; `secrets`
        holds the parsed rows (names and states, never values).
    """
    if not sbx_available():
        return {"available": False, "secrets": []}
    result = run(["sbx", "secret", "ls"], options=RunOptions(check=False))
    if result.returncode != 0:
        log.warning("secret_list_failed", detail=result.stderr.strip()[-500:])
        return {"available": False, "secrets": []}
    return {"available": True, "secrets": parse_secret_table(result.stdout)}


def store_secret(
    service: str,
    *,
    token: str | None = None,
    ref: str | None = None,
    command: str | None = None,
    sandbox: str | None = None,
) -> dict[str, object]:
    """Stores one secret with sbx.

    Exactly one of `token`, `ref` or `command` must be given; the request
    model enforces that before this is called.

    Args:
        service: The sbx service name.
        token: A literal value. Least preferred -- it reaches sbx on the
            command line, where it is briefly visible in `ps`.
        ref: A 1Password `op://` reference or AWS Secrets Manager ARN.
        command: A shell command whose stdout is the secret.
        sandbox: Scope the secret to one sandbox instead of globally.

    Returns:
        `{"stored": True, "service": ..., "method": ...}`.

    Raises:
        RuntimeError: If sbx is unavailable or the command fails. The
            message is built from stderr with the token masked out -- never
            from the argv, which would echo the secret back to the client.
    """
    if not sbx_available():
        raise RuntimeError("sbx is not installed or not on PATH")

    argv = ["sbx", "secret", "set", service]
    if command is not None:
        argv += ["--command", command]
        method = "command"
    elif ref is not None:
        argv += ["--ref", ref]
        method = "ref"
    else:
        # --force is required for --token to overwrite an existing secret.
        argv += ["--token", str(token), "--force"]
        method = "token"
    if sandbox:
        argv += ["--sandbox", sandbox]

    secret_values = [v for v in (token, ref, command) if v]
    # check=False deliberately: CommandError's message embeds the argv, and
    # routes turn that into the HTTP detail, which would leak the token.
    result = run(argv, options=RunOptions(check=False, redact=secret_values))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "sbx secret set failed").strip()
        for value in secret_values:
            detail = detail.replace(value, "***")
        raise RuntimeError(detail[-500:])

    log.info("secret_stored", service=service, method=method, sandbox=sandbox)
    return {"stored": True, "service": service, "method": method}
