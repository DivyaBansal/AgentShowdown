"""Per-job orchestration, split by phase.

- `expand`: features -> named jobs, and queueing them.
- `lifecycle`: launching, resuming, respawning and reattaching a job,
  and running many at once.
- `finish`: polling a launched agent to its outcome, then verifying and
  publishing its work.
"""
