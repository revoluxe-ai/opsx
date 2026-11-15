"""Fail-closed tests for target-native security-signal triage."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


HELPER = (
    Path(__file__).resolve().parents[1] / "security-triage" / "security_triage.py"
)
SCHEMA = HELPER.with_name("SECURITY_FINDING_PACKET.schema.json")
ORIGIN_SOURCE = '''def is_local_soft_prod_origin(origin: str) -> bool:
    return "localhost" in origin or "127.0.0.1" in origin
'''
ORIGIN_SIGNAL = (
    "is_local_soft_prod_origin() uses substring checks "
    '("localhost" in origin, "127.0.0.1" in origin) '
    "instead of strict host parsing. "
    "https://localhost.attacker.tld may satisfy the guard."
)


def _load():
    spec = importlib.util.spec_from_file_location("security_triage", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load security_triage")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    opsx = root / "opsx"
    opsx.mkdir(parents=True)
    _ = (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\nadapters:\n"
        "  - opsx-lite\n  - poetry-python\n",
        encoding="utf-8",
    )


def _origin_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "source": "CURSOR_AGENTIC_SECURITY_REVIEW",
        "signal": ORIGIN_SIGNAL,
        "severity": "MEDIUM",
        "cited_path": "app/payments_psp.py",
        "symbol": "is_local_soft_prod_origin",
    }
    request.update(overrides)
    return request


def _write_origin(root: Path) -> None:
    path = root / "app" / "payments_psp.py"
    path.parent.mkdir(parents=True)
    _ = path.write_text(ORIGIN_SOURCE, encoding="utf-8")


def _findings(root: Path) -> list[Path]:
    base = root / "opsx" / "runs" / "security-cases"
    if not base.exists():
        return []
    return sorted(base.glob("*/findings/*.md"))


def _packet(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    start = text.index("BEGIN_SECURITY_FINDING_PACKET\n") + len(
        "BEGIN_SECURITY_FINDING_PACKET\n"
    )
    end = text.index("\nEND_SECURITY_FINDING_PACKET\n")
    value = json.loads(text[start:end])
    if not isinstance(value, dict):
        raise AssertionError("packet is not an object")
    return value


def test_positive_local_origin_fixture(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    result = module.triage(root, _origin_request())
    assert result["STATUS"] == "CONFIRMED"
    assert result["AFFECTED_PLANE"] == "PRODUCT"
    assert result["SECURITY_FINDING_PACKET"] == "present"
    assert result["CAPABILITY_PARITY"] == "PARTIAL"
    assert result["SECURITY_TRIAGE_DECLARED"] == "NO"
    assert result["SECURITY_REVIEW_VERDICT"] == "not_issued"
    assert result["STATUS"] not in {"PASS", "PASS_WITH_RISK", "FAIL"}
    files = _findings(root)
    assert len(files) == 1
    packet = _packet(files[0])
    assert packet["TRIAGE_STATUS"] == "CONFIRMED"
    assert packet["CONFIDENCE_IS_PROOF"] == "NO"
    assert "substring" in str(packet["SECURITY_INVARIANT"])
    assert "localhost.attacker.tld" in str(packet["RETEST_REQUIREMENT"])
    assert packet["CHANGE_BINDING"] == {
        "CHANGE_ID": "UNBOUND",
        "STATUS": "NEEDS_CHANGE_BINDING",
    }
    assert packet["REMEDIATION_ROUTE"] == "NEEDS_OWNER"
    assert packet["NEXT_COMMAND"] == "none"
    schema = json.loads(SCHEMA.read_text(encoding="utf-8-sig"))
    for key in schema["required"]:
        assert key in packet
    assert not (root / "opsx" / "runs" / "bug-cases").exists()
    assert list(root.rglob("SECURITY-REVIEW.md")) == []


def test_duplicate_and_distinct_signal(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    first = module.triage(root, _origin_request(case_id="SEC-CASE-ORIGIN-0001"))
    second = module.triage(root, _origin_request(case_id="SEC-CASE-ORIGIN-0001"))
    assert first["STATUS"] == "CONFIRMED"
    assert second["STATUS"] == "DUPLICATE"
    assert second["SECURITY_FINDING_PACKET"] == "reused_duplicate"
    assert second["SECURITY_FINDING_ID"] == first["SECURITY_FINDING_ID"]
    assert second["EVIDENCE_PATH"] == first["EVIDENCE_PATH"]
    assert len(_findings(root)) == 1
    third = module.triage(
        root,
        _origin_request(signal=ORIGIN_SIGNAL + " distinct wording"),
    )
    assert third["STATUS"] == "CONFIRMED"
    assert third["SECURITY_FINDING_ID"] != first["SECURITY_FINDING_ID"]
    assert len(_findings(root)) == 2


def test_cross_case_duplicate_does_not_materialize(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    first = module.triage(root, _origin_request(case_id="SEC-CASE-ALPHA-0001"))
    second = module.triage(root, _origin_request(case_id="SEC-CASE-BETA-0002"))
    assert first["STATUS"] == "CONFIRMED"
    assert second["STATUS"] == "DUPLICATE"
    assert second["SECURITY_CASE_ID"] == "SEC-CASE-ALPHA-0001"
    beta = root / "opsx" / "runs" / "security-cases" / "SEC-CASE-BETA-0002"
    assert not beta.exists()
    assert len(_findings(root)) == 1


def test_parent_traversal_denied(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    outside = tmp_path / "outside.txt"
    _ = outside.write_text("OUTSIDE_MARKER", encoding="utf-8")
    result = module.triage(
        root,
        _origin_request(cited_path="../outside.txt", signal="path=../outside.txt"),
    )
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "path_outside_workspace"
    assert "OUTSIDE_MARKER" not in json.dumps(result)
    assert _findings(root) == []


def test_sibling_prefix_and_absolute_outside_denied(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    sibling = tmp_path / "workspace_evil"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    _ = secret.write_text("SIBLING_MARKER", encoding="utf-8")
    absolute = module.triage(root, _origin_request(cited_path=str(secret)))
    assert absolute["STATUS"] == "BLOCKED"
    assert absolute["REASON"] == "path_outside_workspace"
    assert "SIBLING_MARKER" not in json.dumps(absolute)
    embedded = module.triage(
        root,
        _origin_request(signal=f"see {secret}"),
    )
    assert embedded["STATUS"] == "BLOCKED"
    assert embedded["REASON"] == "path_outside_workspace"
    assert _findings(root) == []


def test_evidence_absence_is_not_false_positive(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    result = module.triage(root, _origin_request(confidence="HIGH"))
    assert result["STATUS"] == "NEEDS_CONTEXT"
    assert result["REASON"] == "evidence_absent"
    assert result["SECURITY_FINDING_PACKET"] == "absent"
    assert result["STATUS"] != "FALSE_POSITIVE"
    assert _findings(root) == []
    with pytest.raises(module.SecurityTriageError, match="ABSENCE_IS_NOT_A_DISPOSITION"):
        module.disposition_if_packet_missing()


def test_confidence_alone_does_not_confirm(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    path = root / "app" / "plain.py"
    path.parent.mkdir(parents=True)
    _ = path.write_text("def harmless() -> None:\n    return None\n", encoding="utf-8")
    result = module.triage(
        root,
        {
            "source": "USER_SECURITY_SIGNAL",
            "signal": "This function is definitely vulnerable.",
            "severity": "HIGH",
            "confidence": "HIGH",
            "cited_path": "app/plain.py",
            "evidence_quotes": ["eval("],
        },
    )
    assert result["STATUS"] == "NEEDS_CONTEXT"
    assert result["SECURITY_FINDING_PACKET"] == "absent"
    assert _findings(root) == []


def test_collision_does_not_overwrite(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    request = _origin_request(case_id="SEC-CASE-COLLISION-01")
    digest = module.source_signal_hash(
        source="CURSOR_AGENTIC_SECURITY_REVIEW",
        source_ref="",
        path="app/payments_psp.py",
        symbol="is_local_soft_prod_origin",
        severity="MEDIUM",
        signal=ORIGIN_SIGNAL,
    )
    finding_id = module.finding_id_for(digest)
    target = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / "SEC-CASE-COLLISION-01"
        / "findings"
        / f"{finding_id}.md"
    )
    target.parent.mkdir(parents=True)
    _ = target.write_text("COLLISION_MARKER", encoding="utf-8")
    result = module.triage(root, request)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "identity_collision"
    assert target.read_text(encoding="utf-8") == "COLLISION_MARKER"
    assert result["SECURITY_FINDING_PACKET"] == "absent"


def test_plane_misroute_and_mixed_plane(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    command = root / ".cursor" / "commands" / "opsx-create-pr.md"
    command.parent.mkdir(parents=True)
    _ = command.write_text("plaintext GO gate\n", encoding="utf-8")
    misroute = module.triage(
        root, _origin_request(declared_plane="CONTROL_PLANE")
    )
    assert misroute["STATUS"] == "BLOCKED"
    assert misroute["REASON"] == "plane_misroute"
    assert _findings(root) == []
    mixed = module.triage(
        root,
        _origin_request(
            cited_paths=["app/payments_psp.py", ".cursor/commands/opsx-create-pr.md"]
        ),
    )
    assert mixed["STATUS"] == "BLOCKED"
    assert mixed["REASON"] == "plane_misroute"
    assert _findings(root) == []


def test_false_positive_disproof_writes_nothing(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    path = root / "app" / "payments_psp.py"
    path.parent.mkdir(parents=True)
    _ = path.write_text(
        "def is_local_soft_prod_origin(origin: str) -> bool:\n"
        "    return hostname == 'localhost'\n",
        encoding="utf-8",
    )
    result = module.triage(
        root,
        {
            "source": "MANUAL_SECURITY_REVIEW",
            "signal": "substring localhost check",
            "severity": "MEDIUM",
            "cited_path": "app/payments_psp.py",
            "evidence_quotes": ['"localhost" in'],
            "disproof_quotes": ["hostname == 'localhost'"],
        },
    )
    assert result["STATUS"] == "FALSE_POSITIVE"
    assert result["NEXT_COMMAND"] == "none"
    assert result["SECURITY_FINDING_PACKET"] == "absent"
    assert _findings(root) == []


def test_control_plane_and_infrastructure_routing(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    command = root / ".cursor" / "commands" / "opsx-create-pr.md"
    command.parent.mkdir(parents=True)
    _ = command.write_text("OWNER GO is accepted as plaintext.\n", encoding="utf-8")
    control = module.triage(
        root,
        {
            "source": "CURSOR_AGENTIC_SECURITY_REVIEW",
            "signal": "plaintext GO authorizes publish",
            "severity": "HIGH",
            "cited_path": ".cursor/commands/opsx-create-pr.md",
            "declared_plane": "CONTROL_PLANE",
            "security_class": "authority_provenance",
            "evidence_quotes": ["plaintext"],
            "threat": "plaintext GO can be mistaken for authorization",
            "attack_precondition": "a chat line contains the GO token",
            "causal_path": "the command treats plaintext GO as authority",
            "impact": "unauthorized publish path",
            "security_invariant": "process writes require explicit Owner authorization",
            "remediation_requirement": "keep AUTHORIZE_COMMAND_FIX mandatory",
            "retest_requirement": "plaintext GO must not authorize a write",
            "case_id": "SEC-CASE-CREATE-PR-GO",
        },
    )
    assert control["STATUS"] == "CONFIRMED"
    assert control["AFFECTED_PLANE"] == "CONTROL_PLANE"
    assert control["NEXT_COMMAND"] == "/opsx-meta-fix-command-defects"
    packet = _packet(_findings(root)[0])
    assert packet["REMEDIATION_AUTHORITY_CLASS"] == "PROCESS_COMMAND_REPAIR"
    assert packet["SECURITY_BINDING"]["BINDING_CLASS"] == "PROCESS_CHANGE"
    assert packet["CHANGE_BINDING"]["CHANGE_ID"] == "n/a"
    assert packet["EXPECTED_VERIFIER"] == "/opsx-control-plane-security-review"
    manifest = root / "k8s" / "deploy.yaml"
    manifest.parent.mkdir()
    _ = manifest.write_text("image: registry.example/app:latest\n", encoding="utf-8")
    infra = module.triage(
        root,
        {
            "source": "CI_SECURITY_SIGNAL",
            "signal": "deploy manifest pins a mutable image tag",
            "severity": "MEDIUM",
            "cited_path": "k8s/deploy.yaml",
            "declared_plane": "INFRASTRUCTURE",
            "security_class": "other",
            "evidence_quotes": ["image:"],
            "threat": "mutable image tag can change what the cluster runs",
            "attack_precondition": "the registry tag is overwritten",
            "causal_path": "the manifest references a moving tag",
            "impact": "unreviewed bits can be scheduled",
            "security_invariant": "infra revisions are pinned by digest when required",
            "remediation_requirement": "pin the image by digest via the infra owner",
            "retest_requirement": "the manifest must not use a floating tag",
        },
    )
    assert infra["STATUS"] == "CONFIRMED"
    assert infra["AFFECTED_PLANE"] == "INFRASTRUCTURE"
    assert infra["REMEDIATION_AUTHORITY_CLASS"] == "BLOCKED_AUTHORITY_GAP"
    assert infra["REMEDIATION_ROUTE"] == "BLOCKED"
    assert infra["NEXT_COMMAND"] == "none"
    assert len(_findings(root)) == 2


def test_product_change_binding(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    change = root / "openspec" / "changes" / "sec-origin-fix"
    change.mkdir(parents=True)
    bound = module.triage(root, _origin_request(change_id="sec-origin-fix"))
    assert bound["STATUS"] == "CONFIRMED"
    assert bound["NEXT_COMMAND"] == "/opsx-update"
    assert bound["CHANGE_BINDING"]["STATUS"] == "BOUND"
    assert "security_finding=" in str(bound["NEXT_ARGS"])
    missing = module.triage(
        root,
        _origin_request(
            change_id="missing-change",
            signal=ORIGIN_SIGNAL + " unbound variant",
        ),
    )
    assert missing["STATUS"] == "CONFIRMED"
    assert missing["CHANGE_BINDING"]["STATUS"] == "NEEDS_CHANGE_BINDING"
    assert missing["NEXT_COMMAND"] == "none"


def test_interrupted_write_is_not_confirmed(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    blocked = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / "SEC-CASE-INTERRUPT-01"
        / "findings"
    )
    blocked.parent.mkdir(parents=True)
    _ = blocked.write_text("not-a-directory", encoding="utf-8")
    result = module.triage(
        root, _origin_request(case_id="SEC-CASE-INTERRUPT-01")
    )
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "interrupted_evidence_write"
    assert result["STATUS"] != "CONFIRMED"
    assert _findings(root) == []


def test_out_of_scope_and_projectization_gate(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    path = root / "app" / "note.py"
    path.parent.mkdir(parents=True)
    _ = path.write_text("NOTE = 'ops'\n", encoding="utf-8")
    scoped = module.triage(
        root,
        {
            "source": "OTHER",
            "source_note": "cluster policy report",
            "signal": "network policy belongs to the cluster owner",
            "cited_path": "app/note.py",
            "out_of_scope_authority": "cluster-network-policy",
        },
    )
    assert scoped["STATUS"] == "OUT_OF_SCOPE"
    assert scoped["SECURITY_FINDING_PACKET"] == "absent"
    bare = tmp_path / "bare"
    bare.mkdir()
    blocked = module.triage(bare, _origin_request())
    assert blocked["STATUS"] == "BLOCKED"
    assert blocked["REASON"] == "OPSX_PROJECTIZATION_REQUIRED"


def test_cli_prints_confirmed_packet(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_origin(root)
    request = root / "request.json"
    _ = request.write_text(
        json.dumps(_origin_request()), encoding="utf-8"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo-root",
            str(root),
            "--request-file",
            str(request),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["STATUS"] == "CONFIRMED"
    assert payload["SECURITY_FINDING_PACKET"] == "present"
    assert len(_findings(root)) == 1


def test_symlink_escape_denied(tmp_path: Path) -> None:
    module = _load()
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    outside = tmp_path / "linked.txt"
    _ = outside.write_text("LINK_MARKER", encoding="utf-8")
    link_dir = root / "app"
    try:
        link_dir.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            pytest.skip("symlink not permitted")
    result = module.triage(root, _origin_request(cited_path="app/linked.txt"))
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "path_outside_workspace"
    assert "LINK_MARKER" not in json.dumps(result)
    assert _findings(root) == []
