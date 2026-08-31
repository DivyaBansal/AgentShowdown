"""Thread-scoped logging so concurrent jobs don't interleave unreadably.

`run_all` runs each job on its own worker thread; without a per-job prefix,
stdout from N concurrently polling sandboxes interleaves into an
unreadable stream. `set_job_context` tags the current thread with a
sandbox name (thread-local, since a ThreadPoolExecutor worker only ever
runs one job at a time) and `log` prefixes every line it prints with that
tag when set.
"""

from __future__ import annotations

import sys
import threading
from typing import TextIO

_local = threading.local()


def set_job_context(sandbox_name: str | None) -> None:
    """Tags the current thread's log output with a sandbox name (or clears it)."""
    _local.sandbox_name = sandbox_name


def log(message: str, file: TextIO = sys.stdout) -> None:
    """Prints message, prefixing each line with the current thread's job tag."""
    sandbox_name = getattr(_local, "sandbox_name", None)
    if sandbox_name is None:
        print(message, file=file)
        return
    tag = f"[{sandbox_name}]"
    print("\n".join(f"{tag} {line}" for line in message.split("\n")), file=file)
