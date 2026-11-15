"""Acceptance tests for OPSX dependency closure (opsx-dependency-closure-v0)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_CONTROL_PLANE_TESTS = Path(__file__).resolve().parent
if str(_CONTROL_PLANE_TESTS) not in sys.path:
    sys.path.insert(0, str(_CONTROL_PLANE_TESTS))

from opsx_dependency_closure import (
    CLOSURE_PREDICATE_VERSION,
    DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS,
    evaluate_dependency_closure,
    git_blob_sha256,
    read_activation_rows,
)
from run_command_transfer_contract_tests import check_stroy_target_terminal_transfer

REPO_ROOT = Path(__file__).resolve().parents[3]


def _ensure_repo_openspec_cli_installed() -> None:
    """Bootstrap pinned CLI when node_modules is absent (explicit network)."""

    binary = REPO_ROOT / "opsx_tools/openspec-cli-node/node_modules/.bin/openspec"
    if binary.is_file():
        return
    setup = REPO_ROOT / "opsx_tools/openspec-cli-node/setup.sh"
    _ = subprocess.run([str(setup)], cwd=REPO_ROOT, check=True, capture_output=True)


def _materialize_openspec_cli_provider(root: Path) -> None:
    """Copy pinned OpenSpec CLI provider surfaces into a fixture repo."""

    src_tools = REPO_ROOT / "opsx_tools"
    dst_tools = root / "opsx_tools"
    dst_tools.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_tools / "openspec-cli", dst_tools / "openspec-cli")
    src_node = src_tools / "openspec-cli-node"
    dst_node = dst_tools / "openspec-cli-node"
    dst_node.mkdir(parents=True, exist_ok=True)
    for name in (
        "package.json",
        "package-lock.json",
        "provider.manifest.json",
        "setup.sh",
        "README.md",
    ):
        shutil.copy2(src_node / name, dst_node / name)
    src_modules = src_node / "node_modules"
    if src_modules.is_dir():
        link = dst_node / "node_modules"
        if not link.exists():
            link.symlink_to(src_modules.resolve(), target_is_directory=True)


def _write_minimal_tree(
    root: Path,
    *,
    command_id: str = "opsx-verify",
    include_config: bool = True,
    include_checker: bool = True,
    config_text: str | None = None,
    command_body: str | None = None,
    projectization_status: str = "INCOMPLETE",
    normal_enabled: bool = False,
    extra_activation_command: bool = False,
) -> None:
    """Materialize a minimal OPSX tree for closure tests."""

    _ = subprocess.run(
        ["git", "init"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.email", "closure@test.local"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "closure-test"],
        cwd=root,
        check=True,
        capture_output=True,
    )

    opsx = root / "opsx"
    registry = opsx / "registry"
    registry.mkdir(parents=True)
    commands = root / ".cursor" / "commands"
    commands.mkdir(parents=True)
    (opsx / "project").mkdir(parents=True)
    (opsx / "transfer").mkdir(parents=True)

    capabilities_src = REPO_ROOT / "opsx" / "project" / "capabilities.yaml"
    shutil.copy(capabilities_src, opsx / "project" / "capabilities.yaml")
    shutil.copy(
        REPO_ROOT / "opsx" / "project" / "capabilities.json",
        opsx / "project" / "capabilities.json",
    )
    shutil.copy(
        REPO_ROOT / "opsx" / "project" / "quality-gates.md",
        opsx / "project" / "quality-gates.md",
    )

    body = command_body or (
        "---\n"
        "required_adapters: [\"opsx-lite\", \"project-generic\"]\n"
        "---\n\n"
        f"# /{command_id}\n\n"
        "Run config check:\n"
        "```bash\n"
        "python openspec/tools/check_config_contract.py\n"
        "```\n"
    )
    command_path = commands / f"{command_id}.md"
    _ = command_path.write_text(body, encoding="utf-8")
    command_hash = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()

    rows = [
        {
            "command_id": command_id,
            "runtime_class": "ACTIVE",
            "installed": True,
            "active": True,
            "target_path": f".cursor/commands/{command_id}.md",
            "command_hash": command_hash,
            "identity_mode": "git_blob_sha256_v1",
        }
    ]
    if extra_activation_command:
        disabled_body = (
            "---\nrequired_adapters: [\"opsx-lite\"]\n---\n\n"
            "# /opsx-bugbot-report\n\nLegacy only.\n"
        )
        bug_path = commands / "opsx-bugbot-report.md"
        _ = bug_path.write_text(disabled_body, encoding="utf-8")
        rows.append(
            {
                "command_id": "opsx-bugbot-report",
                "runtime_class": "LEGACY",
                "installed": False,
                "active": False,
                "target_path": ".cursor/commands/opsx-bugbot-report.md",
                "command_hash": __import__("hashlib").sha256(
                    disabled_body.encode("utf-8")
                ).hexdigest(),
                "identity_mode": "git_blob_sha256_v1",
            }
        )

    activation_lines = "\n".join(json.dumps(row) for row in rows)
    _ = (registry / "command_activation.ndjson").write_text(
        activation_lines + "\n",
        encoding="utf-8",
    )

    _ = (opsx / "projectization.yaml").write_text(
        f"status: {projectization_status}\n"
        f"normal_commands_enabled: {'true' if normal_enabled else 'false'}\n"
        f"dependency_closure_predicate_version: {CLOSURE_PREDICATE_VERSION}\n",
        encoding="utf-8",
    )

    _materialize_openspec_cli_provider(root)

    openspec = root / "openspec"
    tools = openspec / "tools"
    tools.mkdir(parents=True)
    if include_checker:
        shutil.copytree(
            REPO_ROOT / "openspec" / "tools",
            tools,
            dirs_exist_ok=True,
        )
    if include_config:
        text = config_text
        if text is None:
            text = (REPO_ROOT / "openspec" / "config.yaml").read_text(encoding="utf-8")
        _ = (openspec / "config.yaml").write_text(text, encoding="utf-8")

    schemas = REPO_ROOT / "openspec" / "schemas"
    if schemas.is_dir():
        shutil.copytree(schemas, openspec / "schemas", dirs_exist_ok=True)

    _ = subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_healthy_installation_passes(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "PASS"
    assert result["identity"]["closure_predicate_version"] == CLOSURE_PREDICATE_VERSION
    assert result["global_failures"] == []


def test_missing_config_fails(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, include_config=False)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "MISSING_MANDATORY" and "config.yaml" in item["path"]
        for item in result["global_failures"]
    )


def test_missing_checker_fails(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, include_checker=False)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "MISSING_MANDATORY"
        and item["path"] == "openspec/tools/check_config_contract.py"
        for item in result["global_failures"]
    )


def test_malformed_config(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, config_text="schema: spec-driven\nrules: [\n")
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "MALFORMED_CONFIG" for item in result["global_failures"])


def test_unsupported_top_level_key(tmp_path: Path) -> None:
    config = (REPO_ROOT / "openspec" / "config.yaml").read_text(encoding="utf-8")
    config += "\ndonor_profile: adv-bot\n"
    _write_minimal_tree(tmp_path, config_text=config)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "UNSUPPORTED_KEY" for item in result["global_failures"])


def test_invalid_rules_shape(tmp_path: Path) -> None:
    config = """schema: spec-driven
rules:
  verify: not-a-mapping
"""
    _write_minimal_tree(tmp_path, config_text=config)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "INVALID_RULES_SHAPE" for item in result["global_failures"])


def test_portable_openspec_guard_line_not_foreign(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-ff\n\n"
        "**OpenSpec guard:** run `python tools/openspec/openspec_guard.py "
        "--scenario opsx_ff --strict`\n"
    )
    guard_path = tmp_path / "tools" / "openspec" / "openspec_guard.py"
    guard_path.parent.mkdir(parents=True)
    _ = guard_path.write_text("# fixture guard\n", encoding="utf-8")
    _write_minimal_tree(tmp_path, command_id="opsx-ff", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert not any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION"
        and "openspec_guard" in item.get("path", "")
        for item in result["global_failures"]
    )


def test_portable_openspec_guard_cite_missing_file_fails(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-ff\n\n"
        "**OpenSpec guard:** run `python tools/openspec/openspec_guard.py "
        "--scenario opsx_ff --strict`\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-ff", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "MISSING_MANDATORY"
        and item["path"] == "tools/openspec/openspec_guard.py"
        and item["command_id"] == "opsx-ff"
        for item in result["global_failures"]
    )


def test_poetry_mention_without_guard_literal_not_foreign(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-ff\n\n"
        "Historical note: donor used Poetry; TARGET uses pip per capabilities.yaml.\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-ff", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert not any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION"
        and "openspec_guard" in item.get("detail", "")
        for item in result["global_failures"]
    )


def test_opsx_propose_obsolete_without_executable_guard(tmp_path: Path) -> None:
    propose_src = (REPO_ROOT / ".cursor" / "commands" / "opsx-propose.md").read_text(
        encoding="utf-8"
    )
    assert "poetry run python tools/openspec/openspec_guard.py" not in propose_src
    assert "stop" in propose_src.lower()
    assert "do **not**" in propose_src or "do not" in propose_src.lower()


def test_openspec_guard_mandatory_detection_wired_in_closure_module() -> None:
    """Removing guard mandatory-path wiring must break closure acceptance."""

    source = (
        REPO_ROOT
        / ".cursor"
        / "pipeline"
        / "control-plane-tests"
        / "opsx_dependency_closure.py"
    ).read_text(encoding="utf-8")
    assert "OPEN_SPEC_GUARD_PATH_PATTERN" in source
    assert "requires_openspec_guard" in source
    assert '"tools/openspec/openspec_guard.py"' in source


def test_foreign_openspec_guard_line(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY\n\n"
        "separate from product Temporal/runtime adoption.\n\n"
        "**OpenSpec guard:** run `poetry run python tools/openspec/openspec_guard.py --strict`\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-ff", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION" for item in result["global_failures"]
    )


def test_donor_assumptions_in_config(tmp_path: Path) -> None:
    config = """schema: spec-driven
rules:
  verify:
    wb:
      - Wildberries execution authority
"""
    _write_minimal_tree(tmp_path, config_text=config)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION" for item in result["global_failures"]
    )


def test_missing_apply_schema_mandatory(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-apply-design\n\n"
        "Schema: `opsx/registry/apply_design.schema.json`\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-apply-design", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "MISSING_MANDATORY"
        and "apply_design.schema.json" in item["path"]
        for item in result["global_failures"]
    )


def test_disabled_command_not_global(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, extra_activation_command=True)
    result = evaluate_dependency_closure(tmp_path)
    assert all(
        item.get("command_id") != "opsx-bugbot-report"
        for item in result["global_failures"]
    )


def test_unresolved_capability_blocks_command_only(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-run-typecheck\n\n"
        "TYPECHECK_ELIGIBLE: pyright-strict\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-run-typecheck", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "PASS"
    assert any(
        block["class"] == "TARGET_CAPABILITY_UNRESOLVED"
        and block["command_id"] == "opsx-run-typecheck"
        for block in result["command_blocks"]
    )


def test_stale_command_identity_after_contract_change(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    verify_path = tmp_path / ".cursor" / "commands" / "opsx-verify.md"
    _ = verify_path.write_text(verify_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "drift"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "STALE_IDENTITY" for item in result["global_failures"])


def test_stale_config_identity_after_prior_pass_receipt(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    first = evaluate_dependency_closure(tmp_path)
    assert first["status"] == "PASS"
    receipt_path = tmp_path / "opsx" / "transfer" / "DEPENDENCY_CLOSURE_RECEIPT.json"
    _ = receipt_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "target_revision": first["target_revision"],
                "identity": first["identity"],
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "openspec" / "config.yaml"
    _ = config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n# drift marker\n",
        encoding="utf-8",
    )
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "config-drift"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    second = evaluate_dependency_closure(tmp_path)
    assert second["status"] == "FAIL"
    assert any(
        item["class"] == "STALE_IDENTITY" and "config_identity" in item.get("detail", "")
        for item in second["global_failures"]
    )


def test_adapter_read_after_secondary_override_heading(tmp_path: Path) -> None:
    """Secondary override headings must not suppress donor adapter foreign reads."""

    body = (
        "---\nrequired_adapters: [\"opsx-lite\"]\n---\n\n"
        "# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY\n\n"
        "separate from product Temporal/runtime adoption.\n\n"
        "# TARGET OVERRIDE — HIGHEST PRIORITY\n\n"
        "### Target authority paths\n\n"
        "Adapter: `opsx/adapters/python-poetry/README.md`\n"
    )
    custom_caps = """schema_version: opsx-target-capabilities-v1
target: closure-fixture
adapter: poetry-python
product:
  package_manager: poetry
rules: {}
"""
    _write_minimal_tree(tmp_path, command_id="opsx-apply-design", command_body=body)
    _ = (tmp_path / "opsx" / "project" / "capabilities.yaml").write_text(
        custom_caps,
        encoding="utf-8",
    )
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "poetry-target"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION"
        and "python-poetry" in item["path"]
        for item in result["global_failures"]
    )


def test_donor_adapter_cite_remapped_on_project_generic_pip_target(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-apply-design\n\n"
        "Adapter: `opsx/adapters/python-poetry/README.md`\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-apply-design", command_body=body)
    pip_caps = """schema_version: opsx-target-capabilities-v1
target: closure-fixture
adapter: project-generic
product:
  package_manager: pip
rules: {}
"""
    _ = (tmp_path / "opsx" / "project" / "capabilities.yaml").write_text(
        pip_caps,
        encoding="utf-8",
    )
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "pip-target"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = evaluate_dependency_closure(tmp_path)
    assert not any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION"
        and "python-poetry" in item["path"]
        for item in result["global_failures"]
    )


def test_real_target_config_passes_config_contract_cli() -> None:
    proc = subprocess.run(
        [sys.executable, "openspec/tools/check_config_contract.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_real_tree_guard_mandatory_paths_satisfied() -> None:
    result = evaluate_dependency_closure(REPO_ROOT)
    assert not any(
        item["class"] == "FOREIGN_DONOR_ASSUMPTION"
        and item["path"] == "tools/openspec/openspec_guard.py"
        for item in result["global_failures"]
    )
    guard_missing = [
        item
        for item in result["global_failures"]
        if item["class"] == "MISSING_MANDATORY"
        and item["path"] == "tools/openspec/openspec_guard.py"
    ]
    assert guard_missing == []


def test_complete_claim_while_foreign_donor_blocks_closure(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-ff\n\n"
        "**OpenSpec guard:** run `poetry run python tools/openspec/openspec_guard.py --strict`\n"
    )
    _write_minimal_tree(
        tmp_path,
        command_id="opsx-ff",
        command_body=body,
        projectization_status="COMPLETE",
        normal_enabled=True,
    )
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "FOREIGN_DONOR_ASSUMPTION" for item in result["global_failures"])
    assert any(item["class"] == "FALSE_COMPLETE_CLAIM" for item in result["global_failures"])


def test_placement_decision_cite_not_install_mandatory(tmp_path: Path) -> None:
    body = (
        "---\nrequired_adapters: [\"opsx-lite\", \"project-generic\"]\n---\n\n"
        "# /opsx-apply-green\n\n"
        "## Architecture Decision References\n\n"
        "- `openspec/decisions/opsx-apply-green-placement/decision.md`\n"
    )
    _write_minimal_tree(tmp_path, command_id="opsx-apply-green", command_body=body)
    result = evaluate_dependency_closure(tmp_path)
    assert not any(
        item["class"] == "MISSING_MANDATORY"
        and "openspec/decisions/" in item["path"]
        for item in result["global_failures"]
    )


def test_false_complete_claim_while_closure_fails(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, include_checker=False, projectization_status="COMPLETE", normal_enabled=True)
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(item["class"] == "FALSE_COMPLETE_CLAIM" for item in result["global_failures"])


def test_predicate_version_drift_in_projectization_fails(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    proj = tmp_path / "opsx" / "projectization.yaml"
    text = proj.read_text(encoding="utf-8")
    text = text.replace(CLOSURE_PREDICATE_VERSION, "v0.0.1-stale")
    proj.write_text(text, encoding="utf-8")
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "stale predicate"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    result = evaluate_dependency_closure(tmp_path)
    assert result["status"] == "FAIL"
    assert any(
        item["class"] == "STALE_IDENTITY"
        and "dependency_closure_predicate_version drift" in item.get("detail", "")
        for item in result["global_failures"]
    )


def test_recovery_after_restore(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path, include_checker=False)
    assert evaluate_dependency_closure(tmp_path)["status"] == "FAIL"
    shutil.copytree(
        REPO_ROOT / "openspec" / "tools",
        tmp_path / "openspec" / "tools",
        dirs_exist_ok=True,
    )
    _ = subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "restore-checker"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    restored = evaluate_dependency_closure(tmp_path)
    assert restored["status"] == "PASS"
    assert restored["identity"]["validator_identity"]["blob_sha256"] is not None


def test_verification_does_not_mutate_product_files(tmp_path: Path) -> None:
    _write_minimal_tree(tmp_path)
    before = subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path).decode()
    _ = evaluate_dependency_closure(tmp_path)
    after = subprocess.check_output(["git", "status", "--porcelain"], cwd=tmp_path).decode()
    assert before == after


def test_check_stroy_target_terminal_transfer_invokes_closure() -> None:
    """Production entry runs closure then transfer acceptance at HEAD."""

    _ensure_repo_openspec_cli_installed()
    result = check_stroy_target_terminal_transfer()
    assert result["status"] == "PASS"
    assert result["schema_version"] == "opsx-dependency-closure-v0"
    assert result.get("transfer_acceptance") == "PASS"


def test_terminal_transfer_source_wires_closure_guard() -> None:
    """Removing the closure gate from the production entry must break this test."""

    source = (
        REPO_ROOT
        / ".cursor"
        / "pipeline"
        / "control-plane-tests"
        / "run_command_transfer_contract_tests.py"
    ).read_text(encoding="utf-8")
    assert "evaluate_dependency_closure" in source
    assert 'closure.get("status") != "PASS"' in source
    assert "run_mandatory_transfer_acceptance_checks" in source
    assert "ACCEPTANCE.json" not in source.split("def check_stroy_target_terminal_transfer")[1].split("def main")[0]


def test_active_activation_command_hash_matches_git_blob_at_head() -> None:
    """Regression: active activation rows must track committed command blob hashes."""

    rows = read_activation_rows(REPO_ROOT / "opsx/registry/command_activation.ndjson")
    mismatches: list[str] = []
    for row in rows:
        if row.get("active") is not True or row.get("installed") is not True:
            continue
        command_id = str(row.get("command_id"))
        if command_id in DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS:
            continue
        relative = f".cursor/commands/{command_id}.md"
        current_hash = git_blob_sha256(REPO_ROOT, relative)
        if current_hash is not None and row.get("command_hash") != current_hash:
            mismatches.append(command_id)
    assert mismatches == []


def test_real_tree_closure_json_artifact(tmp_path: Path) -> None:
    """Persist one real-tree closure JSON run as evidence."""

    _ensure_repo_openspec_cli_installed()
    result = evaluate_dependency_closure(REPO_ROOT)
    artifact = tmp_path / "real_tree_closure.json"
    _ = artifact.write_text(json.dumps(result, indent=2), encoding="utf-8")
    assert result["status"] == "PASS"
    assert result["global_failures"] == []
