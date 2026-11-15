"""Read-only post-deploy acceptance bound to durable devops receipts."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, cast

CONTRACT_ID = "OPSX-WB-POST-DEPLOY-ACCEPTANCE-01"
REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")

def canonical_contained(candidate: Path, approved_root: Path) -> Path | None:
    root = approved_root.resolve()
    raw = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = raw.resolve()
    except OSError:
        return None
    root_text = os.path.normcase(str(root))
    resolved_text = os.path.normcase(str(resolved))
    if resolved_text == root_text or resolved_text.startswith(root_text + os.sep):
        return resolved
    return None

def git_head(root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""

def blocked(reason: str, **extra: object) -> dict[str, object]:
    out: dict[str, object] = {
        "STATUS": "BLOCKED",
        "REASON": reason,
        "PASS": "NO",
        "PRODUCTION_WRITES": "NO",
        "CONTRACT_ID": CONTRACT_ID,
        "GREEN_WRITER": "NO",
    }
    out.update(extra)
    return out

def projectization_block(root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        root,
        "python.pipeline.post_deploy_acceptance",
        blocked_factory=blocked,
    )

def parse_receipt(text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    probes: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip().upper()
        value = value.strip()
        if key == "PROBE":
            probes.append(value)
        elif key:
            values[key] = value
    return values, probes

def run(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str] | None = None,
) -> dict[str, object]:
    root = repo_root.resolve()
    gate = projectization_block(root)
    if gate is not None:
        return gate
    if request.get("GO") in (True, "GO", "true", "YES"):
        return blocked("PRODUCTION_MUTATION_FORBIDDEN")

    relative = str(request.get("deploy_receipt") or "").strip()
    if not relative:
        return blocked("deploy_receipt_required")
    devops_root = (root / "opsx" / "runs" / "devops").resolve()
    receipt = canonical_contained(Path(relative), root)
    if receipt is None or canonical_contained(receipt, devops_root) is None:
        return blocked("receipt_outside_devops_runs")
    if not receipt.is_file():
        return blocked("deploy_receipt_missing")

    text = receipt.read_text(encoding="utf-8-sig", errors="replace")
    values, probes = parse_receipt(text)
    if values.get("STATUS", "").upper() not in {"SUCCESS", "PASS", "DEPLOYED"}:
        return blocked("deploy_not_successful")

    requested_execution = str(request.get("execution_id") or "").strip()
    receipt_execution = values.get("EXECUTION_ID", "")
    if requested_execution and receipt_execution and requested_execution != receipt_execution:
        return blocked("stale_deploy_receipt")

    base_url = str(request.get("base_url") or "").strip()
    if base_url:
        allowed = {
            value
            for key, value in values.items()
            if key in {"BASE_URL", "URL", "ACCEPTANCE_URL"} and value
        }
        if base_url not in allowed:
            return blocked("URL_NOT_ALLOWLISTED")

    catalog = str(request.get("catalog") or "").strip()
    if catalog and not probes:
        return blocked("acceptance_probes_missing")

    receipt_revision = values.get("REVISION", "")
    if receipt_revision:
        head = (head_reader or git_head)(root).strip()
        if not REVISION_RE.fullmatch(head) or receipt_revision.lower() != head.lower():
            return blocked("stale_revision")

    return {
        "STATUS": "PASS",
        "PASS": "YES",
        "CONTRACT_ID": CONTRACT_ID,
        "PRODUCTION_WRITES": "NO",
        "PROBES": probes,
        "DEPLOY_RECEIPT": receipt.relative_to(root).as_posix(),
        "EXECUTION_ID": receipt_execution or requested_execution,
        "AUTHORITY": "/opsx-devops",
        "GREEN_WRITER": "NO",
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request_file.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        print(json.dumps(blocked(f"invalid_request:{exc}"), sort_keys=True))
        return 2
    print(json.dumps(run(args.repo_root, cast(dict[str, object], request)), sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
