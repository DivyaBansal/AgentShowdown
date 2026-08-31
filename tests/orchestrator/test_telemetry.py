"""Tests for agentshowdown.orchestrator.telemetry."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from agentshowdown.orchestrator import telemetry as tel
from agentshowdown.orchestrator.telemetry import (
    TelemetrySampler,
    compute_cpu_cores,
    parse_cgroup_probe,
)

if TYPE_CHECKING:
    import pytest

# Values as actually observed inside a live sbx v0.39.0 sandbox.
REAL_PROBE = (
    "cpu_usage_usec=1388409\nmem_current=278622208\nmem_max=max\npids=59\nmem_total_kb=32940968\n"
)


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class _Config:
    telemetry = True
    telemetry_interval_seconds = 5


def test_parses_a_real_sandbox_probe() -> None:
    reading = parse_cgroup_probe(REAL_PROBE)
    assert reading.cpu_usage_usec == 1388409
    assert reading.mem_bytes == 278622208
    assert reading.pids == 59


def test_unlimited_memory_falls_back_to_host_total() -> None:
    """cgroup reports "max" when the sandbox was created without --memory,
    so a utilisation percentage still needs a denominator."""
    assert parse_cgroup_probe(REAL_PROBE).mem_limit_bytes == 32940968 * 1024


def test_explicit_memory_limit_is_used_when_set() -> None:
    probe = REAL_PROBE.replace("mem_max=max", "mem_max=2147483648")
    assert parse_cgroup_probe(probe).mem_limit_bytes == 2147483648


def test_missing_files_yield_none_rather_than_raising() -> None:
    """A cgroup v1 sandbox or hardened image reports nothing at all."""
    empty = "cpu_usage_usec=\nmem_current=\nmem_max=\npids=\nmem_total_kb=\n"
    reading = parse_cgroup_probe(empty)
    assert reading.is_empty
    assert reading.mem_limit_bytes is None


def test_garbage_output_is_tolerated() -> None:
    for bad in ("", "not key=value", "cpu_usage_usec=abc\n", "\x00\x01"):
        assert parse_cgroup_probe(bad).cpu_usage_usec is None


def test_cpu_cores_is_the_rate_between_cumulative_readings() -> None:
    # 2s of CPU time over 1s of wall clock == 2 cores busy.
    assert compute_cpu_cores(0, 2_000_000, 1.0) == 2.0
    assert compute_cpu_cores(1_000_000, 1_500_000, 1.0) == 0.5


def test_cpu_cores_is_none_on_the_first_sample() -> None:
    assert compute_cpu_cores(None, 1_000_000, 5.0) is None


def test_cpu_cores_is_none_when_the_counter_goes_backwards() -> None:
    """A restarted container resets the counter; a negative rate is wrong."""
    assert compute_cpu_cores(5_000_000, 1_000_000, 1.0) is None


def test_cpu_cores_is_none_for_a_non_positive_interval() -> None:
    assert compute_cpu_cores(0, 1_000_000, 0) is None


def test_sampler_does_not_start_a_thread_when_telemetry_is_off() -> None:
    """The default path must cost nothing at all, not merely little."""

    class Off(_Config):
        telemetry = False

    sampler = TelemetrySampler(Off())
    sampler.start()
    assert sampler._thread is None
    sampler.stop()


def test_sampler_starts_a_thread_when_enabled() -> None:
    sampler = TelemetrySampler(_Config())
    sampler.start()
    try:
        assert sampler._thread is not None
        assert sampler._thread.is_alive()
    finally:
        sampler.stop()
    assert sampler._thread is None


def test_sample_once_probes_each_live_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execs: list[str] = []

    def fake_exec(name, cmd, **kw):
        execs.append(name)
        return _completed(REAL_PROBE)

    monkeypatch.setattr(tel, "sbx_list", lambda: {"box-1": {}, "box-2": {}})
    monkeypatch.setattr(tel, "sbx_exec_capture", fake_exec)

    sampler = TelemetrySampler(_Config())
    samples = sampler.sample_once(1.0, "2026-08-31T00:00:00Z")

    # One exec per sandbox per pass -- never more.
    assert execs == ["box-1", "box-2"]
    assert {s.sandbox_name for s in samples} == {"box-1", "box-2"}
    assert all(s.mem_bytes == 278622208 for s in samples)


def test_cpu_rate_appears_on_the_second_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    readings = iter([REAL_PROBE, REAL_PROBE.replace("1388409", "3388409")])
    monkeypatch.setattr(tel, "sbx_list", lambda: {"box-1": {}})
    monkeypatch.setattr(tel, "sbx_exec_capture", lambda *a, **k: _completed(next(readings)))

    sampler = TelemetrySampler(_Config())
    first = sampler.sample_once(1.0, "t0")
    second = sampler.sample_once(1.0, "t1")

    assert first[0].cpu_cores is None  # nothing to compare against yet
    assert second[0].cpu_cores == 2.0  # 2s of CPU over 1s of wall clock


def test_a_failed_probe_yields_an_empty_sample_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sandbox that exits mid-sample must not fail the run."""
    monkeypatch.setattr(tel, "sbx_list", lambda: {"box-1": {}})
    monkeypatch.setattr(tel, "sbx_exec_capture", lambda *a, **k: _completed("", returncode=1))

    samples = TelemetrySampler(_Config()).sample_once(1.0, "t0")

    assert len(samples) == 1
    assert samples[0].mem_bytes is None
    assert samples[0].cpu_cores is None


def test_on_sample_callback_receives_each_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tel, "sbx_list", lambda: {"box-1": {}})
    monkeypatch.setattr(tel, "sbx_exec_capture", lambda *a, **k: _completed(REAL_PROBE))
    received = []

    TelemetrySampler(_Config(), on_sample=received.append).sample_once(1.0, "t0")

    assert len(received) == 1
    assert received[0].pids == 59


def test_probe_never_uses_a_login_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs: dict[str, object] = {}
    monkeypatch.setattr(tel, "sbx_list", lambda: {"box-1": {}})

    def fake_exec(name, cmd, **kw):
        kwargs.update(kw)
        return _completed(REAL_PROBE)

    monkeypatch.setattr(tel, "sbx_exec_capture", fake_exec)
    TelemetrySampler(_Config()).sample_once(1.0, "t0")

    assert kwargs.get("login_shell") is False
