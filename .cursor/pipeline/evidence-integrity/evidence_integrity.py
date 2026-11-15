"""S-EV1: read-only verifier целостности apply-receipt под ``openspec/changes/<id>/``.

Сверяет git-объекты и пути коммитов с контрактом EVIDENCE_INTEGRITY_ADOPTION_V0.
Не пишет receipt, ledger, hook и не выдаёт adoption authority. Вывод — один JSON
на stdout; код выхода 0 при успешном отчёте, даже при непустых findings.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CONTRACT_ID = "EVIDENCE_INTEGRITY_ADOPTION_V0"
INPUT_SCHEMA = "opsx.s_ev1_input"
REPORT_SCHEMA = "opsx.s_ev1_report"

OWNER_ATTESTATION_PREFIX = re.compile(
    r"^(store:|cursor-stores/|/cursor/stores/|owner-message:)"
)

INFO_CODES = frozenset(
    {
        "ATTESTATION_CONTENT_UNVERIFIED",
        "LEGACY_UNBOUND_REVISION",
        "LEGACY_UNBOUND_PRODUCTION",
        "ALLOWLIST_UNBOUND",
    }
)

CHECKS_NOT_RUN: tuple[str, ...] = (
    "T2_REEXECUTION",
    "ATTESTATION_CONTENT",
    "DISPATCH_INDEPENDENCE",
    "CAUSAL_DIFFERENTIAL",
    "VACUOUS_ASSERTION",
    "GREEN_INPUT_KEY",
    "COMPLETION_CLASS",
    "LATCH_LEDGER",
)

FROZEN_DIGEST_KEYS = frozenset(
    {
        "test_content_identity",
        "frozen_test_content_identity",
        "frozen_test_identity_before",
        "frozen_test_identity_after",
    }
)

OVERCLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bVERIFIED_AT_TIER_T2\b"), "VERIFIED_AT_TIER_T2"),
    (re.compile(r"\bPLATFORM_ATTESTED\b"), "PLATFORM_ATTESTED"),
    (re.compile(r"\bTIER_T3\b|\bT3_PLATFORM\b"), "T3"),
    (re.compile(r"\bVERIFIED\b"), "VERIFIED"),
)

LIVE_AUTHORITY_FORBIDDEN = re.compile(
    r"execution-authority-gate-lab|S4b.*live|LIVE_AUTHORITY\s*:\s*(?!NO)",
    re.IGNORECASE,
)


class EvidenceIntegrityError(RuntimeError):
    """Фатальная ошибка верификатора (не git repo, сбой subprocess)."""


@dataclass
class Finding:
    """Одна находка S-EV1."""

    code: str
    severity: str
    tier: str
    evidence: str
    slice_id: str | None = None
    path: str | None = None
    introducing_oid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "tier": self.tier,
            "evidence": self.evidence,
        }
        if self.slice_id is not None:
            out["slice_id"] = self.slice_id
        if self.path is not None:
            out["path"] = self.path
        if self.introducing_oid is not None:
            out["introducing_oid"] = self.introducing_oid
        return out


@dataclass
class VerifierState:
    """Накопитель findings и метаданных прогона."""

    findings: list[Finding] = field(default_factory=list)
    checks_run: set[str] = field(default_factory=set)
    legacy_flags: set[str] = field(default_factory=set)
    max_tier: str = "T1"

    def add(self, finding: Finding) -> None:
        self.findings.append(finding)
        if finding.tier == "T0":
            if self.max_tier == "T1" and all(f.tier == "T0" for f in self.findings):
                self.max_tier = "T0"
        if finding.code in ("FORMAT_VALID_ONLY",) and finding.tier == "T0":
            self.max_tier = "T0"

    def recompute_max_tier(self) -> None:
        if not self.findings:
            self.max_tier = "T1"
            return
        self.max_tier = "T1" if any(f.tier == "T1" for f in self.findings) else "T0"


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise EvidenceIntegrityError(
            f"git {' '.join(args)} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def _git_ok(repo: Path, *args: str) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _posix(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _matches_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern)


def _paths_under_prefix(path: str, prefix: str) -> bool:
    p = _posix(path)
    pre = _posix(prefix).rstrip("/")
    return p == pre or p.startswith(pre + "/")


def _parse_json_at_commit(repo: Path, commit: str, rel_path: str) -> dict[str, Any] | None:
    if not _git_ok(repo, "cat-file", "-e", f"{commit}:{rel_path}"):
        return None
    raw = _git(repo, "show", f"{commit}:{rel_path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return data
    return None


def _blob_at_commit(repo: Path, commit: str, rel_path: str) -> str | None:
    if not _git_ok(repo, "cat-file", "-e", f"{commit}:{rel_path}"):
        return None
    return _git(repo, "show", f"{commit}:{rel_path}")


def _commits_touching_change(repo: Path, change_root: str) -> list[str]:
    """Коммиты, достижимые с HEAD и затрагивающие change_root."""

    root = _posix(change_root).rstrip("/")
    log = _git(repo, "log", "--format=%H", "HEAD", "--", root)
    if not log:
        return []
    return [line for line in log.splitlines() if line]


def _files_in_commit(repo: Path, commit: str) -> list[str]:
    if commit.endswith("^"):
        return []
    out = _git(
        repo,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-only",
        "-r",
        commit,
    )
    return [_posix(p) for p in out.splitlines() if p]


def _introducing_commit(
    repo: Path, commits_newest_first: Sequence[str], rel_path: str
) -> str | None:
    """Первый коммит (от старого к новому), где путь появился или изменился."""

    for commit in reversed(commits_newest_first):
        if rel_path in _files_in_commit(repo, commit):
            return commit
    return None


def _parse_production_surfaces(design_text: str, slice_id: str) -> list[str] | None:
    """Извлечь поверхности из fenced JSON в design.md."""

    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", design_text, re.DOTALL)
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        surfaces = data.get("evidence_integrity_production_surfaces")
        if not isinstance(surfaces, dict):
            continue
        raw = surfaces.get(slice_id)
        if raw is None:
            continue
        if isinstance(raw, list):
            return [_posix(str(p)) for p in raw]
    return None


def _discover_receipt_paths(repo_root: Path, change_root: str) -> list[str]:
    """Receipt paths относительно repo root."""

    rels: list[str] = []
    base = repo_root / change_root
    if not base.is_dir():
        return rels
    for stage in ("apply-red", "apply-implement", "apply-green"):
        stage_base = base / stage
        if not stage_base.is_dir():
            continue
        for path in stage_base.rglob("*"):
            if path.suffix.lower() in {".json", ".md"} and path.is_file():
                rels.append(_posix(str(path.relative_to(repo_root))))
    for path in base.rglob("CURRENT-BYTE-ADOPTION-AUTHORITY-*.json"):
        if path.is_file():
            rels.append(_posix(str(path.relative_to(repo_root))))
    return sorted(set(rels))


def _stage_from_path(rel_path: str) -> str | None:
    p = _posix(rel_path)
    if "/apply-red/" in p:
        return "RED"
    if "/apply-implement/" in p:
        return "IMPLEMENT"
    if "/apply-green/" in p:
        return "GREEN"
    if "CURRENT-BYTE-ADOPTION-AUTHORITY" in p:
        return "AUTHORITY"
    return None


def _slice_from_path(rel_path: str) -> str | None:
    m = re.search(r"/(apply-red|apply-implement|apply-green)/([^/]+)/", _posix(rel_path))
    if m:
        return m.group(2)
    m2 = re.search(r"/(apply-implement|apply-green|apply-red)/([^/]+)/", _posix(rel_path))
    return m2.group(2) if m2 else None


def _severity_for(code: str) -> str:
    return "INFO" if code in INFO_CODES else "HARD"


def _scan_overclaim(
    state: VerifierState,
    text: str,
    data: Mapping[str, Any],
    *,
    path: str,
    slice_id: str | None,
) -> None:
    state.checks_run.add("OVERCLAIM_SCAN")
    if data.get("self_asserted_strong_verdict") is True:
        state.add(
            Finding(
                code="FORMAT_VALID_ONLY",
                severity="HARD",
                tier="T0",
                evidence="receipt offers strong verdict without git recompute",
                path=path,
                slice_id=slice_id,
            )
        )
    for pattern, label in OVERCLAIM_PATTERNS:
        if pattern.search(text):
            state.add(
                Finding(
                    code="OVERCLAIM_VERDICT",
                    severity="HARD",
                    tier="T0",
                    evidence=f"forbidden token {label}",
                    path=path,
                    slice_id=slice_id,
                )
            )


def _scan_live_authority(
    state: VerifierState,
    data: Mapping[str, Any],
    raw: str,
    *,
    path: str,
    slice_id: str | None,
) -> None:
    state.checks_run.add("LIVE_AUTHORITY_SCAN")
    live = data.get("live_authority")
    if live is not None and str(live) != "NO":
        state.add(
            Finding(
                code="LIVE_AUTHORITY_CLAIM_FORBIDDEN",
                severity="HARD",
                tier="T0",
                evidence=f"live_authority={live!r}",
                path=path,
                slice_id=slice_id,
            )
        )
    if LIVE_AUTHORITY_FORBIDDEN.search(raw):
        state.add(
            Finding(
                code="LIVE_AUTHORITY_CLAIM_FORBIDDEN",
                severity="HARD",
                tier="T0",
                evidence="text cites forbidden live authority / S4 gate",
                path=path,
                slice_id=slice_id,
            )
        )


def _check_attestation_ref(
    state: VerifierState,
    ref: str,
    *,
    path: str,
    slice_id: str | None,
) -> None:
    state.checks_run.add("ATTESTATION_REF_FORMAT")
    if OWNER_ATTESTATION_PREFIX.match(ref):
        state.add(
            Finding(
                code="ATTESTATION_CONTENT_UNVERIFIED",
                severity="INFO",
                tier="T1",
                evidence=f"prefix ok for {ref!r}",
                path=path,
                slice_id=slice_id,
            )
        )
    else:
        state.add(
            Finding(
                code="ATTESTATION_REF_NOT_OWNER_HELD",
                severity="HARD",
                tier="T1",
                evidence=f"ref {ref!r} outside Owner-held prefixes",
                path=path,
                slice_id=slice_id,
            )
        )


def _json_field_changed(intro: dict[str, Any], head: dict[str, Any], key: str) -> bool:
    return intro.get(key) != head.get(key)


def _collect_digest_fields(obj: Any, prefix: str = "") -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key_path = f"{prefix}.{k}" if prefix else k
            if k in FROZEN_DIGEST_KEYS and isinstance(v, str):
                found[key_path] = v
            found.update(_collect_digest_fields(v, key_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.update(_collect_digest_fields(item, f"{prefix}[{i}]"))
    return found


def verify_s_ev1(
    input_payload: Mapping[str, Any],
    *,
    narrowed_commits: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Построить отчёт S-EV1 по входу ``opsx.s_ev1_input`` v1.

    Args:
        input_payload: Поля schema opsx.s_ev1_input (caller hints; HEAD из git).
        narrowed_commits: Только для тестов SEV1-N-RANGE — урезанный список oid.

    Returns:
        Словарь отчёта opsx.s_ev1_report v1.

    Raises:
        EvidenceIntegrityError: Репозиторий не git или сбой git.
    """

    state = VerifierState()
    repo_root = Path(str(input_payload["repo_root"])).resolve()
    change_root = _posix(str(input_payload["change_root"]))
    contour = str(input_payload["contour"])
    filter_slice = input_payload.get("slice_id")
    expect_head = input_payload.get("expect_head_oid")

    if not _git_ok(repo_root, "rev-parse", "HEAD"):
        raise EvidenceIntegrityError("not a git repository")

    head_oid = _git(repo_root, "rev-parse", "HEAD")
    if expect_head and expect_head != head_oid:
        state.add(
            Finding(
                code="INPUT_HEAD_MISMATCH",
                severity="HARD",
                tier="T1",
                evidence=f"expect {expect_head}, git HEAD {head_oid}",
            )
        )
        state.checks_run.add("INPUT_HEAD")

    full_commits = _commits_touching_change(repo_root, change_root)
    commits = list(full_commits)
    if narrowed_commits is not None:
        narrowed = [_posix(c) for c in narrowed_commits]
        dropped = [c for c in full_commits if c not in narrowed]
        if dropped:
            state.add(
                Finding(
                    code="RANGE_NARROWED",
                    severity="HARD",
                    tier="T1",
                    evidence=f"dropped commits still scanned: {','.join(dropped[:3])}",
                )
            )
            state.checks_run.add("COMMIT_RANGE")
        commits = full_commits

    receipt_paths = _discover_receipt_paths(repo_root, change_root)
    if filter_slice:
        receipt_paths = [p for p in receipt_paths if f"/{filter_slice}/" in p]

    state.checks_run.add("RECEIPT_DISCOVERY")

    red_status_by_slice: dict[str, tuple[str, str]] = {}
    green_red_status_by_slice: dict[str, tuple[str, str]] = {}

    for rel_path in receipt_paths:
        stage = _stage_from_path(rel_path)
        slice_id = _slice_from_path(rel_path)
        intro_oid = _introducing_commit(repo_root, commits, rel_path)
        if intro_oid is None:
            continue

        intro_blob = _blob_at_commit(repo_root, intro_oid, rel_path)
        head_blob = _blob_at_commit(repo_root, head_oid, rel_path)
        if intro_blob is None or head_blob is None:
            continue

        state.checks_run.add("RECEIPT_IMMUTABILITY")
        if intro_blob != head_blob:
            state.add(
                Finding(
                    code="RECEIPT_MUTATED_AFTER_STAGE",
                    severity="HARD",
                    tier="T1",
                    evidence="blob at HEAD differs from introducing commit",
                    path=rel_path,
                    slice_id=slice_id,
                    introducing_oid=intro_oid,
                )
            )
            if rel_path.endswith(".json"):
                intro_j = json.loads(intro_blob)
                head_j = json.loads(head_blob)
                if _json_field_changed(intro_j, head_j, "status"):
                    state.add(
                        Finding(
                            code="STATUS_RESTAMP",
                            severity="HARD",
                            tier="T1",
                            evidence="status field changed after introduction",
                            path=rel_path,
                            slice_id=slice_id,
                            introducing_oid=intro_oid,
                        )
                    )
                intro_red = intro_j.get("red_identity")
                head_red = head_j.get("red_identity")
                if isinstance(intro_red, dict) and isinstance(head_red, dict):
                    if intro_red.get("status") != head_red.get("status"):
                        state.add(
                            Finding(
                                code="STATUS_RESTAMP",
                                severity="HARD",
                                tier="T1",
                                evidence="red_identity.status changed after introduction",
                                path=rel_path,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )
                intro_d = _collect_digest_fields(intro_j)
                head_d = _collect_digest_fields(head_j)
                for key, val in intro_d.items():
                    if key in head_d and head_d[key] != val:
                        state.add(
                            Finding(
                                code="RED_IDENTITY_REWRITE",
                                severity="HARD",
                                tier="T1",
                                evidence=f"{key} changed after introduction",
                                path=rel_path,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )

        if rel_path.endswith(".json"):
            head_j = json.loads(head_blob)
            _scan_overclaim(
                state, head_blob, head_j, path=rel_path, slice_id=slice_id
            )
            _scan_live_authority(
                state, head_j, head_blob, path=rel_path, slice_id=slice_id
            )
            if stage == "RED" and slice_id and "status" in head_j:
                red_status_by_slice[slice_id] = (str(head_j["status"]), rel_path)
            if stage == "GREEN" and slice_id:
                red_id = head_j.get("red_identity")
                if isinstance(red_id, dict) and "status" in red_id:
                    green_red_status_by_slice[slice_id] = (
                        str(red_id["status"]),
                        rel_path,
                    )
                if "pre_impl_revision" not in head_j:
                    state.legacy_flags.add("LEGACY_UNBOUND_REVISION")
                    state.add(
                        Finding(
                            code="LEGACY_UNBOUND_REVISION",
                            severity="INFO",
                            tier="T1",
                            evidence="GREEN missing pre_impl_revision (legacy)",
                            path=rel_path,
                            slice_id=slice_id,
                        )
                    )
                if "green_input_key" not in head_j:
                    state.legacy_flags.add("LEGACY_UNBOUND_PRODUCTION")
                    state.add(
                        Finding(
                            code="LEGACY_UNBOUND_PRODUCTION",
                            severity="INFO",
                            tier="T1",
                            evidence="GREEN missing green_input_key (legacy)",
                            path=rel_path,
                            slice_id=slice_id,
                        )
                    )
            if stage == "AUTHORITY" or "owner_attestation_ref" in head_j:
                ref = head_j.get("owner_attestation_ref")
                if isinstance(ref, str):
                    _check_attestation_ref(
                        state, ref, path=rel_path, slice_id=slice_id
                    )

        intro_files = _files_in_commit(repo_root, intro_oid)
        design_rel = f"{change_root}/design.md"
        design_text = _blob_at_commit(repo_root, intro_oid, design_rel) or ""

        if stage in {"RED", "IMPLEMENT", "GREEN"} and slice_id:
            allowed: set[str] = set()
            implement_allowlist_enforced = True
            for meta in (
                "design.md",
                "proposal.md",
                "tasks.md",
                "owner-contract.md",
            ):
                allowed.add(f"{change_root}/{meta}")
            if stage == "RED" and rel_path.endswith(".json"):
                red_j = json.loads(intro_blob or head_blob)
                for tp in red_j.get("test_paths") or []:
                    allowed.add(_posix(str(tp)))
                for fp in red_j.get("fixture_paths") or []:
                    allowed.add(_posix(str(fp)))
                prefix = f"{change_root}/apply-red/{slice_id}/"
                for f in intro_files:
                    if _paths_under_prefix(f, prefix):
                        allowed.add(f)
            elif stage == "IMPLEMENT":
                prefix = f"{change_root}/apply-implement/{slice_id}/"
                for f in intro_files:
                    if _paths_under_prefix(f, prefix):
                        allowed.add(f)
                surfaces = _parse_production_surfaces(design_text, slice_id)
                if surfaces is None:
                    state.add(
                        Finding(
                            code="ALLOWLIST_UNBOUND",
                            severity="INFO",
                            tier="T1",
                            evidence="no evidence_integrity_production_surfaces in design.md",
                            slice_id=slice_id,
                        )
                    )
                else:
                    allowed.update(surfaces)
                implement_allowlist_enforced = surfaces is not None
            elif stage == "GREEN":
                prefix = f"{change_root}/apply-green/{slice_id}/"
                for f in intro_files:
                    if _paths_under_prefix(f, prefix):
                        allowed.add(f)
                pred = None
                if rel_path.endswith(".json"):
                    gj = json.loads(intro_blob or head_blob)
                    pred = gj.get("predecessor_branch")
                if pred != "ADOPTION":
                    surfaces = _parse_production_surfaces(design_text, slice_id)
                    if surfaces is None:
                        state.add(
                            Finding(
                                code="ALLOWLIST_UNBOUND",
                                severity="INFO",
                                tier="T1",
                                evidence="no production surfaces for GREEN NORMAL",
                                slice_id=slice_id,
                            )
                        )
                    else:
                        allowed.update(surfaces)

            state.checks_run.add("STAGE_BOUNDARY")
            for touched in intro_files:
                if stage == "IMPLEMENT":
                    if touched.startswith("tests/") or "/tests/" in touched:
                        state.add(
                            Finding(
                                code="STAGE_BOUNDARY_VIOLATION",
                                severity="HARD",
                                tier="T1",
                                evidence=f"IMPLEMENT commit touched test {touched}",
                                path=touched,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )
                        state.add(
                            Finding(
                                code="FROZEN_RED_MUTATED",
                                severity="HARD",
                                tier="T1",
                                evidence="tests or fixtures changed in IMPLEMENT commit",
                                path=touched,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )
                        continue
                if not _paths_under_prefix(touched, change_root):
                    continue
                if stage == "RED":
                    if touched.endswith((".json", ".md")) and (
                        "/apply-implement/" in touched or "/apply-green/" in touched
                    ):
                        state.add(
                            Finding(
                                code="STAGE_BOUNDARY_VIOLATION",
                                severity="HARD",
                                tier="T1",
                                evidence=f"RED commit touched {touched}",
                                path=touched,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )
                    if touched.startswith("tests/") or "/tests/" in touched:
                        continue
                    if any(_paths_under_prefix(touched, a) for a in allowed):
                        continue
                    if _paths_under_prefix(touched, f"{change_root}/apply-red/"):
                        continue
                elif stage == "IMPLEMENT":
                    if "/apply-red/" in touched:
                        state.add(
                            Finding(
                                code="STAGE_BOUNDARY_VIOLATION",
                                severity="HARD",
                                tier="T1",
                                evidence=f"IMPLEMENT commit touched RED tree {touched}",
                                path=touched,
                                slice_id=slice_id,
                                introducing_oid=intro_oid,
                            )
                        )
                    if (
                        implement_allowlist_enforced
                        and allowed
                        and not any(
                            _paths_under_prefix(touched, a) for a in allowed
                        )
                    ):
                        if not _paths_under_prefix(
                            touched, f"{change_root}/apply-implement/"
                        ):
                            state.add(
                                Finding(
                                    code="STAGE_BOUNDARY_VIOLATION",
                                    severity="HARD",
                                    tier="T1",
                                    evidence=f"path {touched} outside IMPLEMENT allowlist",
                                    path=touched,
                                    slice_id=slice_id,
                                    introducing_oid=intro_oid,
                                )
                            )
                elif stage == "GREEN":
                    if allowed and not any(
                        _paths_under_prefix(touched, a) for a in allowed
                    ):
                        if not _paths_under_prefix(touched, f"{change_root}/apply-green/"):
                            state.add(
                                Finding(
                                    code="STAGE_BOUNDARY_VIOLATION",
                                    severity="HARD",
                                    tier="T1",
                                    evidence=f"path {touched} outside GREEN allowlist",
                                    path=touched,
                                    slice_id=slice_id,
                                    introducing_oid=intro_oid,
                                )
                            )

    if contour == "product":
        state.checks_run.add("PRODUCT_CONTROL_PLANE_BOUNDARY")
        for commit in commits:
            for touched in _files_in_commit(repo_root, commit):
                if _paths_under_prefix(touched, "opsx/") or _paths_under_prefix(
                    touched, ".cursor/"
                ):
                    state.add(
                        Finding(
                            code="STAGE_BOUNDARY_VIOLATION",
                            severity="HARD",
                            tier="T1",
                            evidence=f"product contour touched control plane {touched}",
                            path=touched,
                            introducing_oid=commit,
                        )
                    )
    elif contour == "control_plane":
        state.checks_run.add("CONTROL_PLANE_BOUNDARY")
        for commit in commits:
            for touched in _files_in_commit(repo_root, commit):
                if not _paths_under_prefix(touched, change_root):
                    continue
                if _paths_under_prefix(touched, "opsx/") or (
                    _paths_under_prefix(touched, ".cursor/")
                    and not _paths_under_prefix(touched, ".cursor/pipeline/")
                ):
                    state.add(
                        Finding(
                            code="STAGE_BOUNDARY_VIOLATION",
                            severity="HARD",
                            tier="T1",
                            evidence=f"control_plane contour touched forbidden {touched}",
                            path=touched,
                            introducing_oid=commit,
                        )
                    )

    state.checks_run.add("CROSS_RECEIPT_STATUS")
    for slice_id, (green_status, gpath) in green_red_status_by_slice.items():
        red_entry = red_status_by_slice.get(slice_id)
        if red_entry is None:
            continue
        red_status, rpath = red_entry
        if green_status != red_status:
            state.add(
                Finding(
                    code="CROSS_RECEIPT_STATUS_MISMATCH",
                    severity="HARD",
                    tier="T1",
                    evidence=f"GREEN red_identity.status={green_status!r} RED.status={red_status!r}",
                    path=gpath,
                    slice_id=slice_id,
                )
            )

    state.recompute_max_tier()
    if state.findings and all(f.code == "FORMAT_VALID_ONLY" for f in state.findings):
        state.max_tier = "T0"

    findings_sorted = sorted(
        state.findings,
        key=lambda f: (f.code, f.path or "", f.slice_id or "", f.evidence),
    )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for f in findings_sorted:
        key = (f.code, f.path or "", f.slice_id or "", f.evidence)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f.as_dict())

    return {
        "schema": REPORT_SCHEMA,
        "schema_version": 1,
        "live_authority": "NO",
        "platform_attestation": "UNPROVEN",
        "max_tier_reached": state.max_tier,
        "verified_at_tier_t2": "NOT_EMITTED",
        "completion_class": "NOT_EMITTED",
        "apply_complete": "NOT_EMITTED",
        "gating": "DETECT_ONLY",
        "head_oid": head_oid,
        "change_root": change_root,
        "contour": contour,
        "findings": deduped,
        "checks_run": sorted(state.checks_run),
        "checks_not_run": list(CHECKS_NOT_RUN),
        "legacy_flags": sorted(state.legacy_flags),
        "writes": [],
    }


def _load_input(path: Path | None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise EvidenceIntegrityError("input must be a JSON object")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: JSON in → JSON out на stdout."""

    parser = argparse.ArgumentParser(description="S-EV1 evidence integrity verifier")
    _ = parser.add_argument(
        "--input",
        type=Path,
        help="Path to opsx.s_ev1_input JSON (default: stdin)",
    )
    args = parser.parse_args(argv)
    try:
        payload = _load_input(args.input)
        report = verify_s_ev1(payload)
    except EvidenceIntegrityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"invalid input JSON: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
