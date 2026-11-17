"""CLI for a real, durable LangGraph OPSX verification lane.

Usage: python -m tools.langgraph_runtime.cli run --change CHANGE --execute
       python -m tools.langgraph_runtime.cli status --change CHANGE
       python -m tools.langgraph_runtime.cli resume --change CHANGE --execute
"""
from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from .backend import RealVerificationBackend
from opsx_cli.codex_provider import DEFAULT_CODEX_MODEL
from .journal import DispatchJournal
from .verification import build_verification_graph


DEFAULT_REPO = Path(__file__).resolve().parents[2]


@contextmanager
def exclusive_change_lock(path: Path):
    """Cross-process OS lock; automatically released after process death."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as file:
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"\0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another LangGraph run holds this change") from exc
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another LangGraph run holds this change") from exc
            try:
                yield
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _summary(state, run_id: str):
    data = state.values or {}
    terminal = data.get("terminal") or {}
    return {
        "run_id": run_id,
        "change": data.get("change"),
        "provider": data.get("provider", "cursor"),
        "model_id": data.get("model_id"),
        "status": data.get("status", "NOT_STARTED"),
        "next": list(state.next or ()),
        "next_check_runs": data.get("next_check_runs", 0),
        "executor_runs": data.get("executor_runs", 0),
        "block_reason": terminal.get("block_reason"),
        "next_action": terminal.get("next_action"),
    }


def ensure_resume_provider_identity(snapshot, *, provider: str, codex_model: str) -> None:
    """Prevent a resumed ledger from silently changing provider or model."""
    values = snapshot.values or {}
    if not values:
        return
    previous_provider = values.get("provider", "cursor")
    if previous_provider != provider:
        raise RuntimeError("PROVIDER_IDENTITY_MISMATCH: existing checkpoint belongs to another provider")
    if previous_provider == "codex" and values.get("model_id") != codex_model:
        raise RuntimeError("MODEL_IDENTITY_MISMATCH: model substitution is not permitted")


def main(argv=None):
    p = argparse.ArgumentParser(description="Durable real OPSX verification on LangGraph")
    p.add_argument("command", choices=["run", "resume", "status"])
    p.add_argument("--change", required=True, help="Existing OpenSpec change ID")
    p.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    p.add_argument("--run-id", help="Explicit new identity for another verification cycle")
    p.add_argument("--model-config")
    p.add_argument("--provider", choices=("cursor", "codex"), default="cursor")
    p.add_argument("--codex-model", default=DEFAULT_CODEX_MODEL)
    p.add_argument("--execute", action="store_true", help="Required for external model execution")
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    if not (resource_path(repo, ".git")).exists():
        p.error(f"Repository not found: {repo}")
    if not args.change or "/" in args.change or "\\" in args.change or ".." in args.change:
        p.error("Invalid change ID")
    if args.command in ("run", "resume") and not args.execute:
        p.error("Provider execution requires --execute")

    run_id = args.run_id or f"verification:{args.change}"
    root = resource_path(repo, "opsx", "runs", "langgraph")
    root.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(args.change.encode("utf-8")).hexdigest() + ".lock"
    cfg = {"configurable": {"thread_id": run_id}}
    # Checkpoint and side-effect journal are separate durable SQLite stores.
    with exclusive_change_lock(resource_path(root, "locks", lock_name)):
        with SqliteSaver.from_conn_string(str(resource_path(root, "checkpoints.sqlite3"))) as saver:
            saver.setup()
            backend = RealVerificationBackend(
                repo, run_id=run_id, model_config=args.model_config,
                provider=args.provider, codex_model=args.codex_model
            )
            journal = DispatchJournal(resource_path(root, "dispatch.sqlite3"))
            graph = build_verification_graph(
                backend, journal, repo=repo, checkpointer=saver
            )
            snapshot = graph.get_state(cfg)
            if args.command == "resume":
                ensure_resume_provider_identity(
                    snapshot, provider=args.provider, codex_model=args.codex_model,
                )
            if args.command == "status":
                outcome = _summary(snapshot, run_id)
            elif args.command == "run":
                if snapshot.values:
                    raise RuntimeError("Run already has checkpoints; use resume/status or a new --run-id")
                graph.invoke(
                    {"run_id": run_id, "change": args.change,
                     "provider": args.provider,
                     "model_id": args.codex_model if args.provider == "codex" else None},
                    config=cfg,
                    durability="sync",
                )
                outcome = _summary(graph.get_state(cfg), run_id)
            else:
                if not snapshot.values:
                    raise RuntimeError("No checkpoint to resume; use run")
                if snapshot.next:
                    graph.invoke(None, config=cfg, durability="sync")
                outcome = _summary(graph.get_state(cfg), run_id)
    print(json.dumps(outcome, indent=2, ensure_ascii=False))
    return 0 if outcome["status"] == "ALL_REQUIRED_CHECKS_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
