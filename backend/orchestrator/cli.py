"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
import time

from backend.orchestrator.config import Config, Feature
from backend.orchestrator.job import expand_jobs, resume_job, run_all
from backend.orchestrator.process import CommandError
from backend.orchestrator.state import StateStore


def _cell(job: dict, key: str) -> str:
    """Renders one status-table cell, treating None as an empty string."""
    value = job.get(key)
    return "" if value is None else str(value)


def print_status_table(jobs: list[dict]) -> None:
    """Prints a plain fixed-width table of job rows.

    Args:
        jobs: Rows from StateStore.all_jobs().
    """
    if not jobs:
        print("No jobs recorded yet.")
        return
    columns = [
        "sandbox_name",
        "feature_id",
        "agent_id",
        "status",
        "branch",
        "pr_url",
        "updated_at",
    ]
    widths = {c: max(len(c), *(len(_cell(j, c)) for j in jobs)) for c in columns}
    header = "  ".join(c.ljust(widths[c]) for c in columns) + "  detail"
    print(header)
    print("-" * len(header))
    for j in jobs:
        row = "  ".join(_cell(j, c).ljust(widths[c]) for c in columns)
        detail = (j.get("detail") or "").replace("\n", " ")[:60]
        print(f"{row}  {detail}")


def build_arg_parser() -> argparse.ArgumentParser:
    """Builds the top-level CLI parser with its run/status/answer subcommands."""
    parser = argparse.ArgumentParser(description="Coding-agent orchestrator.")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--features", default="config/features.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run feature(s) through their configured agent(s).")
    g = run_p.add_mutually_exclusive_group(required=True)
    g.add_argument("--feature-id", action="append", help="Feature id to run (repeatable).")
    g.add_argument("--all", action="store_true", help="Run every feature in the features file.")
    run_p.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Override run.max_concurrency from config for this invocation.",
    )

    status_p = sub.add_parser("status", help="Show all known jobs.")
    status_p.add_argument(
        "--watch",
        action="store_true",
        help="Keep refreshing at the configured poll interval.",
    )

    answer_p = sub.add_parser("answer", help="Answer a job that is awaiting_input.")
    answer_p.add_argument("sandbox_name")
    answer_p.add_argument("answer_text")

    return parser


def main() -> None:
    """Parses CLI args and dispatches to the requested subcommand."""
    parser = build_arg_parser()
    args = parser.parse_args()

    try:
        config = Config.load(args.config)
        features = Feature.load_all(args.features)

        if args.command == "run":
            feature_ids = None if args.all else args.feature_id
            jobs = expand_jobs(config, features, feature_ids)
            max_workers = args.max_concurrency or config.max_concurrency
            run_all(config, jobs, max_workers)
            print_status_table(StateStore(config.state_db_path).all_jobs())
        elif args.command == "status":
            store = StateStore(config.state_db_path)
            while True:
                print_status_table(store.all_jobs())
                if not args.watch:
                    break
                time.sleep(config.poll_interval_seconds)
                print()
        elif args.command == "answer":
            resume_job(config, features, args.sandbox_name, args.answer_text)
    except CommandError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
