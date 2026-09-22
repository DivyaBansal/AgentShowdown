"""Sandbox resource sampling.

`sbx` has no `stats`/`top` command, and calling `docker` directly would
bypass the isolation, credential proxying and network policy that sbx
provides -- so usage is read from inside the container instead, via one
`sbx exec` per sandbox per tick.

That cost is why this is opt-in. A default job never starts a sampler at
all: `TelemetrySampler.start` returns without spawning a thread when
`config.telemetry` is false, so the cost is genuinely zero rather than
merely small.

Locating the cgroup files
-------------------------
A sandbox sees ``/sys/fs/cgroup`` as the *root* cgroup, where ``cpu.stat``
exists but ``memory.current`` and ``memory.max`` deliberately do not. The
container's own files live under the path in ``/proc/self/cgroup`` (e.g.
``/docker/<id>``). Verified inside a live sbx v0.39.0 sandbox: reading
``/sys/fs/cgroup$(cat /proc/self/cgroup | sed 's|^0::||')/memory.current``
returns real bytes while the root path returns ENOENT.

Every reading is optional. A sandbox on cgroup v1, a hardened image, or a
container that exits mid-sample yields ``None`` fields rather than an
exception -- telemetry must never fail a job.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.logging import log
from backend.orchestrator.sbx import sbx_exec_capture, sbx_list

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.orchestrator.config import Config

# One shell command, emitting labelled key=value lines. Self-locating: it
# resolves the container's own cgroup path rather than assuming one, and
# every read is guarded so a missing file yields an empty value instead of
# a non-zero exit.
CGROUP_PROBE = r"""
REL=$(sed -n 's|^0::||p' /proc/self/cgroup 2>/dev/null)
D=/sys/fs/cgroup$REL
echo "cpu_usage_usec=$(awk '/^usage_usec/{print $2}' "$D/cpu.stat" 2>/dev/null)"
echo "mem_current=$(cat "$D/memory.current" 2>/dev/null)"
echo "mem_max=$(cat "$D/memory.max" 2>/dev/null)"
echo "pids=$(cat "$D/pids.current" 2>/dev/null)"
echo "mem_total_kb=$(awk '/^MemTotal/{print $2}' /proc/meminfo 2>/dev/null)"
"""


@dataclass(frozen=True)
class CgroupReading:
    """One raw probe result. Every field is optional by design."""

    cpu_usage_usec: int | None = None
    mem_bytes: int | None = None
    mem_limit_bytes: int | None = None
    pids: int | None = None

    @property
    def is_empty(self) -> bool:
        """Whether the probe returned nothing usable."""
        return self.cpu_usage_usec is None and self.mem_bytes is None and self.pids is None


@dataclass(frozen=True)
class Sample:
    """A resource sample for one sandbox at one instant."""

    sandbox_name: str
    ts: str
    cpu_cores: float | None
    mem_bytes: int | None
    mem_limit_bytes: int | None
    pids: int | None


def _as_int(value: str) -> int | None:
    """Parses an int, returning None for empty or non-numeric input."""
    value = value.strip()
    if not value or not value.lstrip("-").isdigit():
        return None
    return int(value)


def parse_cgroup_probe(output: str) -> CgroupReading:
    """Parses the probe's key=value output.

    Args:
        output: Raw stdout from CGROUP_PROBE.

    Returns:
        The reading, with None for anything absent or unparseable. Never
        raises -- a sandbox that can't report is not a failed job.
    """
    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key.strip()] = value

    mem_limit = _as_int(fields.get("mem_max", ""))
    if mem_limit is None:
        # cgroup reports the literal "max" when no limit was set (no
        # --memory at create time). Fall back to host RAM so a utilisation
        # percentage still has a denominator.
        total_kb = _as_int(fields.get("mem_total_kb", ""))
        mem_limit = total_kb * 1024 if total_kb is not None else None

    return CgroupReading(
        cpu_usage_usec=_as_int(fields.get("cpu_usage_usec", "")),
        mem_bytes=_as_int(fields.get("mem_current", "")),
        mem_limit_bytes=mem_limit,
        pids=_as_int(fields.get("pids", "")),
    )


def compute_cpu_cores(
    previous_usec: int | None, current_usec: int | None, elapsed_seconds: float
) -> float | None:
    """Converts two cumulative CPU readings into cores used.

    `cpu.stat:usage_usec` is cumulative CPU time, so the rate between two
    samples is the number of cores busy over that interval: 1.0 means one
    core saturated, 2.5 means two and a half.

    Args:
        previous_usec: The prior cumulative reading, or None on first sample.
        current_usec: The current cumulative reading.
        elapsed_seconds: Wall-clock time between the two.

    Returns:
        Cores used, or None when it can't be computed (first sample, a
        missing reading, a non-positive interval, or a counter that went
        backwards -- which means the container restarted).
    """
    if previous_usec is None or current_usec is None or elapsed_seconds <= 0:
        return None
    delta = current_usec - previous_usec
    if delta < 0:
        return None
    return round(delta / 1_000_000 / elapsed_seconds, 4)


def sample_sandbox(
    sandbox_name: str,
    previous_usec: int | None,
    elapsed_seconds: float,
    now: str,
) -> tuple[Sample, int | None]:
    """Takes one reading from a sandbox.

    Args:
        sandbox_name: Sandbox to probe.
        previous_usec: Prior cumulative CPU reading, for rate calculation.
        elapsed_seconds: Wall time since that reading.
        now: Timestamp to record.

    Returns:
        (sample, cpu_usage_usec) -- the second value feeds the next call's
        `previous_usec`.
    """
    result = sbx_exec_capture(sandbox_name, CGROUP_PROBE, login_shell=False)
    reading = parse_cgroup_probe(result.stdout if result.returncode == 0 else "")
    sample = Sample(
        sandbox_name=sandbox_name,
        ts=now,
        cpu_cores=compute_cpu_cores(previous_usec, reading.cpu_usage_usec, elapsed_seconds),
        mem_bytes=reading.mem_bytes,
        mem_limit_bytes=reading.mem_limit_bytes,
        pids=reading.pids,
    )
    return sample, reading.cpu_usage_usec


class TelemetrySampler:
    """Samples every live sandbox on one shared background thread.

    One thread for all sandboxes, not one per job: each sample costs a
    process inside a container, so a single pass per interval bounds the
    cost at N execs per tick regardless of how many jobs are in flight.
    """

    def __init__(self, config: Config, on_sample: Callable[[Sample], None] | None = None) -> None:
        """
        Args:
            config: Orchestrator config (supplies `telemetry` and
                `telemetry_interval_seconds`).
            on_sample: Optional callable invoked with each Sample, for
                pushing to subscribers.
        """
        self._config = config
        self._on_sample = on_sample
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_usec: dict[str, int] = {}

    def start(self) -> None:
        """Starts sampling, unless telemetry is disabled.

        When `config.telemetry` is false this returns without creating a
        thread at all, so the default path costs nothing.
        """
        if not self._config.telemetry:
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="telemetry-sampler", daemon=True)
        self._thread.start()
        log.info(
            "telemetry_sampler_started",
            interval_seconds=self._config.telemetry_interval_seconds,
        )

    def stop(self) -> None:
        """Stops sampling and waits briefly for the thread to exit."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def sample_once(self, elapsed_seconds: float, now: str) -> list[Sample]:
        """Samples every currently-listed sandbox exactly once.

        Args:
            elapsed_seconds: Wall time since the previous pass.
            now: Timestamp to record on each sample.

        Returns:
            One Sample per live sandbox.
        """
        samples: list[Sample] = []
        # Host-side listing: free, and it keeps us from exec-ing into a
        # sandbox that has already gone away.
        for sandbox_name in sbx_list():
            sample, usec = sample_sandbox(
                sandbox_name, self._last_usec.get(sandbox_name), elapsed_seconds, now
            )
            if usec is not None:
                self._last_usec[sandbox_name] = usec
            samples.append(sample)
            if self._on_sample is not None:
                self._on_sample(sample)
        return samples

    def _loop(self) -> None:
        interval = self._config.telemetry_interval_seconds
        last = time.monotonic()
        while not self._stop.wait(interval):
            now = time.monotonic()
            elapsed, last = now - last, now
            try:
                self.sample_once(elapsed, dt.datetime.now(dt.UTC).isoformat())
            except Exception:  # noqa: BLE001 -- sampling must never kill a run
                log.warning("telemetry_sample_failed", exc_info=True)
