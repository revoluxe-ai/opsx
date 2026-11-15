"""OPSX: reviewed real SDK recordings stored as immutable Git fixtures.

Inbox is NOT evidence. Promotion is a separate explicit, reviewed operation;
no model invocation, shell execution, or automatic Git commit happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

_SCHEMA = "opsx.recorded_sdk.v1"
_REPORT_CONTRACT = "scenario_repair_replay_only.v1"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_FILE_SUFFIXES = {
    "assistant.txt": ".assistant.txt",
    "conversation.json": ".conversation.json",
    "stream.ndjson": ".ndjson",
    "tests.diff": ".tests.diff",
    "report.json": ".json",
}
_REPORT_KEYS = {
    "assistant.txt": "assistant_output",
    "conversation.json": "conversation_output",
    "stream.ndjson": "stream_output",
    "tests.diff": "full_test_diff",
}
_MAX_SINGLE_BYTES = 40 * 1024 * 1024
_SENSITIVE = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{12,}"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(rb'(?i)(?:api[_-]?key|authorization|access[_-]?token)\s*["\x27]?\s*[:=]\s*["\x27]?(?:Bearer\s+)?[A-Za-z0-9_.-]{20,}'),
)


class RecordingRejected(ValueError):
    """Do not promote or replay incomplete, tampered, or unreviewed evidence."""


def _assert(condition: bool, code: str) -> None:
    if not condition:
        raise RecordingRejected(code)


def _read(path: Path) -> bytes:
    _assert(path.is_file() and not path.is_symlink(), "RECORDING_ARTIFACT_MISSING_OR_UNSAFE")
    _assert(path.stat().st_size <= _MAX_SINGLE_BYTES, "RECORDING_ARTIFACT_TOO_LARGE")
    return path.read_bytes()


def _digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _json(blob: bytes) -> dict[str, Any]:
    try:
        data = json.loads(blob.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RecordingRejected("RECORDING_INVALID_JSON") from exc
    _assert(isinstance(data, dict), "RECORDING_JSON_NOT_OBJECT")
    return data


def _report_identity(report: dict[str, Any]) -> dict[str, Any]:
    _assert(report.get("contract") == _REPORT_CONTRACT, "RECORDING_REPORT_CONTRACT_MISMATCH")
    _assert(report.get("production_gate_evidence") is False, "RECORDING_IS_NOT_GATE_EVIDENCE")
    _assert(report.get("model_call_status") == "SDK_RECEIPTED", "RECORDING_SDK_NOT_RECEIPTED")
    _assert(report.get("recording_complete") is True, "RECORDING_INCOMPLETE")
    _assert(report.get("checkout_removed") is True, "RECORDING_CHECKOUT_NOT_REMOVED")
    _assert(report.get("source_git_status_unchanged") is True, "RECORDING_SOURCE_MUTATED")
    sha = report.get("requested_commit_sha")
    _assert(isinstance(sha, str) and _SHA.fullmatch(sha) is not None, "RECORDING_REVISION_UNBOUND")
    _assert(report.get("actual_checkout_sha") == sha, "RECORDING_CHECKOUT_REVISION_MISMATCH")
    _assert(report.get("source_head_after") == sha, "RECORDING_SOURCE_REVISION_MISMATCH")
    model = report.get("model")
    _assert(isinstance(model, dict) and isinstance(model.get("id"), str) and bool(model["id"]), "RECORDING_MODEL_UNBOUND")
    for field in ("sdk_run_id", "recorded_run", "change_id"):
        _assert(isinstance(report.get(field), str) and bool(report[field].strip()), f"RECORDING_{field.upper()}_UNBOUND")
    gaps = report.get("recorded_gap_ids")
    _assert(isinstance(gaps, list) and bool(gaps) and all(isinstance(x, str) and x for x in gaps) and len(gaps) == len(set(gaps)), "RECORDING_GAP_IDENTITY_INVALID")
    identity = {
        "source_sha": sha,
        "model_id": model["id"],
        "sdk_run_id": report["sdk_run_id"],
        "recorded_run": report["recorded_run"],
        "change_id": report["change_id"],
        "recorded_gap_ids": gaps,
        "candidate_status": report.get("candidate_status"),
    }
    # Исторические raw-отчёты могут не иметь capture_execution_id.
    # Для новых записей привязываем к manifest также уникальный ID SDK-вызова,
    # а не только ID исторического audited run.
    capture_id = report.get("capture_execution_id")
    if capture_id is not None:
        _assert(
            isinstance(capture_id, str)
            and re.fullmatch(r"scenario-repair-[0-9a-f]{32}", capture_id) is not None,
            "RECORDING_CAPTURE_EXECUTION_ID_INVALID",
        )
        identity["capture_execution_id"] = capture_id
    return identity


def _parser_observation(answer: bytes, repo_root: Path) -> dict[str, str]:
    """Use the production JSON/Pydantic parser; model output is not a gate verdict."""
    runtime = str(repo_root / "opsx" / "opsx_runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from agents.repair.scenario_test_repair.schemas import ScenarioTestRepairAnswer
    from agents.utils.response import parse_agent_json

    try:
        result = parse_agent_json(answer.decode("utf-8"), ScenarioTestRepairAnswer)
    except (ValueError, UnicodeError) as exc:
        return {"outcome": "INVALID", "error_type": type(exc).__name__}
    return {"outcome": "PARSED", "status": result.status}


def _artifact_bytes(report_path: Path, report: dict[str, Any]) -> dict[str, bytes]:
    data: dict[str, bytes] = {}
    for name, suffix in _FILE_SUFFIXES.items():
        source = report_path if name == "report.json" else report_path.with_suffix(suffix)
        if name in _REPORT_KEYS:
            declared = report.get(_REPORT_KEYS[name])
            _assert(isinstance(declared, str) and Path(declared).resolve() == source.resolve(), "RECORDING_SIDECAR_PATH_MISMATCH")
        data[name] = _read(source)
    _assert(_digest(data["assistant.txt"]) == report.get("assistant_sha256"), "RECORDING_ASSISTANT_DIGEST_MISMATCH")
    try:
        conv = json.loads(data["conversation.json"].decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise RecordingRejected("RECORDING_CONVERSATION_INVALID") from exc
    _assert(isinstance(conv, list) and bool(conv), "RECORDING_CONVERSATION_INCOMPLETE")
    return data


def _scan_secrets(artifacts: dict[str, bytes]) -> None:
    for name, blob in artifacts.items():
        for pattern in _SENSITIVE:
            _assert(pattern.search(blob) is None, f"RECORDING_POSSIBLE_SECRET:{name}")


def _fixture_path(repo_root: Path, identity: dict[str, Any], report_blob: bytes) -> Path:
    identity_key = _digest((identity["sdk_run_id"] + "\0" + _digest(report_blob)).encode("utf-8"))[:16]
    return repo_root / "opsx/tests/recordings/scenario_test_repair" / (identity["source_sha"][:12] + "-" + identity_key)


def verify_fixture(path: Path, repo_root: Path) -> dict[str, Any]:
    """Recheck every byte, source identity and parser behavior, without SDK."""
    _assert(path.is_dir() and not path.is_symlink(), "RECORDING_DIRECTORY_MISSING")
    _assert(path.resolve().is_relative_to((repo_root / "opsx/tests/recordings").resolve()), "RECORDING_OUTSIDE_TRACKED_ROOT")
    manifest = _json(_read(path / "manifest.json"))
    _assert(manifest.get("schema") == _SCHEMA and manifest.get("review_attested") is True, "RECORDING_NOT_REVIEWED")
    expected_names = set(_FILE_SUFFIXES) | {"manifest.json"}
    _assert({p.name for p in path.iterdir()} == expected_names, "RECORDING_UNEXPECTED_ARTIFACTS")
    checks = manifest.get("artifacts")
    _assert(isinstance(checks, dict) and set(checks) == set(_FILE_SUFFIXES), "RECORDING_ARTIFACT_SET_MISMATCH")
    artifacts: dict[str, bytes] = {}
    for name in _FILE_SUFFIXES:
        blob = _read(path / name)
        expected = checks.get(name)
        _assert(isinstance(expected, dict) and expected.get("sha256") == _digest(blob) and expected.get("bytes") == len(blob), "RECORDING_ARTIFACT_DIGEST_MISMATCH")
        artifacts[name] = blob
    _scan_secrets(artifacts)
    report = _json(artifacts["report.json"])
    identity = _report_identity(report)
    _assert(manifest.get("identity") == identity, "RECORDING_IDENTITY_MISMATCH")
    _assert(_digest(artifacts["assistant.txt"]) == report.get("assistant_sha256"), "RECORDING_ASSISTANT_DIGEST_MISMATCH")
    _assert(manifest.get("parser_observation") == _parser_observation(artifacts["assistant.txt"], repo_root), "RECORDING_PARSER_REGRESSION")
    return manifest


def promote(report_path: Path, repo_root: Path, *, reviewed: bool = False) -> Path:
    """Explicit, fail-closed copy from raw inbox to Git-tracked fixtures."""
    _assert(reviewed, "RECORDING_OWNER_REVIEW_REQUIRED")
    report_path = report_path.resolve(strict=True)
    repo_root = repo_root.resolve(strict=True)
    _assert((repo_root / ".git").exists(), "RECORDING_REPOSITORY_UNBOUND")
    report_blob = _read(report_path)
    report = _json(report_blob)
    identity = _report_identity(report)
    artifacts = _artifact_bytes(report_path, report)
    _scan_secrets(artifacts)
    observation = _parser_observation(artifacts["assistant.txt"], repo_root)
    target = _fixture_path(repo_root, identity, report_blob)
    manifest = {
        "schema": _SCHEMA,
        "review_attested": True,  # Human review flag, not machine proof of safety.
        "identity": identity,
        "parser_observation": observation,
        "artifacts": {name: {"sha256": _digest(blob), "bytes": len(blob)} for name, blob in artifacts.items()},
        "gate_evidence": False,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Одна исходная ревизия + один SDK run = максимум ОДНА запись.
    # Atomic mkdir резервирует identity от двух параллельных promotions.
    # Если процесс был прерван, остаточный lock требует review/recovery,
    # но никогда не даёт молча заменить существующую запись.
    lock_key = _digest(
        (identity["source_sha"] + "\0" + identity["sdk_run_id"]).encode("utf-8")
    )[:32]
    lock_dir = target.parent / (".promote-lock-" + lock_key)
    try:
        lock_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise RecordingRejected("RECORDING_PROMOTION_LOCKED") from exc
    try:
        for prior in sorted(target.parent.iterdir()):
            if not prior.is_dir() or prior.name.startswith("."):
                continue
            existing = verify_fixture(prior, repo_root)
            previous_identity = existing["identity"]
            if (
                previous_identity["source_sha"] == identity["source_sha"]
                and previous_identity["sdk_run_id"] == identity["sdk_run_id"]
            ):
                # Идемпотентный повтор идентичной записи — только READ,
                # без перезаписи файлов, manifest или диалогов.
                _assert(
                    prior == target and existing == manifest,
                    "RECORDING_DUPLICATE_RUN_CONFLICT",
                )
                return prior
        _assert(not target.exists(), "RECORDING_EXISTING_CONFLICT")
        with tempfile.TemporaryDirectory(prefix=".promote-", dir=target.parent) as tmp:
            staging = Path(tmp)
            for name, blob in artifacts.items():
                (staging / name).write_bytes(blob)
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            verify_fixture(staging, repo_root)
            _assert(not target.exists(), "RECORDING_EXISTING_CONFLICT")
            staging.rename(target)  # same-filesystem atomic publication
        return target
    finally:
        lock_dir.rmdir()


def _verify_sdk_recordings(repo_root: Path) -> int:
    store = repo_root / "opsx/tests/recordings/scenario_test_repair"
    if store.is_dir():
        pending = [
            entry for entry in store.iterdir()
            if entry.name.startswith((".promote-", ".promote-lock-"))
        ]
        _assert(not pending, "RECORDING_PROMOTION_RECOVERY_REQUIRED")
    folders = sorted(
        entry for entry in store.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ) if store.is_dir() else []
    for folder in folders:
        verify_fixture(folder, repo_root)
    return len(folders)


def _load_historical_migration_module():
    """Загрузить historical_migration рядом с этим скриптом (не зависит от repo_root)."""
    path = Path(__file__).resolve().parent / "historical_migration.py"
    spec = importlib.util.spec_from_file_location("historical_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("HISTORICAL_MIGRATION_MODULE_MISSING")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _verify_historical_partial(repo_root: Path) -> int:
    historical_root = repo_root / "opsx/tests/recordings"
    count = 0
    if not historical_root.is_dir():
        return 0
    verify_historical_fixture = _load_historical_migration_module().verify_historical_fixture

    for agent_dir in sorted(historical_root.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name == "scenario_test_repair":
            continue
        for fixture in sorted(agent_dir.iterdir()):
            if not fixture.is_dir() or fixture.name.startswith("."):
                continue
            if not (fixture / "manifest.json").is_file():
                continue
            manifest = verify_historical_fixture(fixture)
            _assert(
                manifest.get("recording_complete_for_sdk_replay") is False,
                "HISTORICAL_MUST_NOT_PASS_AS_SDK_COMPLETE",
            )
            count += 1
    return count


def main() -> int:
    cli = argparse.ArgumentParser(description="Review and freeze real OPSX SDK recordings for offline replay")
    cli.add_argument("--repo-root", type=Path, default=Path.cwd())
    cli.add_argument("--report", type=Path, help="real replay report JSON, with adjacent SDK sidecars")
    cli.add_argument("--reviewed", action="store_true", help="explicit attestation that raw artifacts were inspected")
    cli.add_argument(
        "--verify-all",
        action="store_true",
        help="verify full SDK scenario_test_repair fixtures only; BLOCKED when count is zero",
    )
    cli.add_argument(
        "--verify-historical-partial",
        action="store_true",
        help="verify historical REAL_PARTIAL manifests/digests only",
    )
    cli.add_argument(
        "--verify-inventory",
        action="store_true",
        help="print SDK + historical counts; SDK gate status reported separately",
    )
    args = cli.parse_args()
    try:
        root = args.repo_root.resolve()
        if args.verify_all:
            sdk_count = _verify_sdk_recordings(root)
            _assert(sdk_count > 0, "RECORDING_NO_REAL_FIXTURES")
            print(f"VERIFIED_RECORDINGS={sdk_count}")
            return 0
        if args.verify_historical_partial:
            historical_count = _verify_historical_partial(root)
            print(f"VERIFIED_HISTORICAL_PARTIAL={historical_count}")
            return 0
        if args.verify_inventory:
            sdk_count = 0
            try:
                sdk_count = _verify_sdk_recordings(root)
            except RecordingRejected:
                sdk_count = 0
            historical_count = _verify_historical_partial(root)
            sdk_gate = "PASS" if sdk_count > 0 else "BLOCKED"
            print(
                "INVENTORY "
                f"SDK_RECORDINGS={sdk_count} "
                f"HISTORICAL_PARTIAL={historical_count} "
                f"SDK_GATE={sdk_gate}"
            )
            return 0
        _assert(args.report is not None, "RECORDING_REPORT_REQUIRED")
        print(promote(args.report, args.repo_root, reviewed=args.reviewed))
        return 0
    except (RecordingRejected, OSError) as exc:
        print(f"RECORDING_BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())