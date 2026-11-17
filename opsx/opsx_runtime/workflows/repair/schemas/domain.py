"""Domain contracts for the restored LangGraph Bug Team / repair workflow."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent import (
    DEFAULT_AGENT_INPUT_LIMIT,
    ModelSelection,
    WorkerReceipt,
)
from .preflight import PreflightReport


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Severity = Literal["critical", "high", "medium", "low"]
ClassificationKind = Literal[
    "IMPLEMENTATION_BUG",
    "NEW_REQUIREMENT",
    "ARCHIVED_PLANNING_BUG",
    "SPEC_BUG",
    "SPEC_GAP",
    "FALSE_POSITIVE",
    "OUT_OF_SCOPE",
    "INCONCLUSIVE",
]
RelationType = Literal[
    "SAME_INVARIANT_FINDING",
    "NEW_ROOT_CAUSE",
    "FALSE_POSITIVE",
    "OUT_OF_SCOPE",
    "INCONCLUSIVE",
]





class SourceFinding(StrictModel):
    id: str | None = None
    bug_id: str | None = None
    severity: Severity
    location: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    root_relation: str = "ROOT"
    evidence_paths: list[str] = Field(default_factory=list)


class FindingReport(StrictModel):
    status: Literal["CLEAN", "FINDINGS", "BLOCKED", "NO_DIFF"]
    findings: list[SourceFinding] = Field(default_factory=list)
    block_reason: str | None = None

    @model_validator(mode="after")
    def report_consistency(self) -> "FindingReport":
        if self.status == "FINDINGS" and not self.findings:
            raise ValueError("FINDINGS requires at least one finding")
        if self.status != "FINDINGS" and self.findings:
            raise ValueError("findings allowed only with FINDINGS status")
        if self.status == "BLOCKED" and not self.block_reason:
            raise ValueError("BLOCKED requires block_reason")
        return self


class Finding(StrictModel):
    id: str = Field(min_length=1)
    bug_id: str | None = None
    finding: str = Field(min_length=1)
    location: str = Field(min_length=1)
    severity: Severity = "medium"
    root_relation: str = "ROOT"
    evidence_paths: list[str] = Field(default_factory=list)


class ClassificationPayload(StrictModel):
    status: Literal["PASS", "BLOCKED"]
    kind: ClassificationKind
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    chosen_route: Literal[
        "ROUTE_A",
        "ROUTE_B",
        "ROUTE_C",
        "ROUTE_D",
        "ROUTE_NEW_CHANGE",
        "ROUTE_ARCHIVED_DELTA",
        "BLOCKED",
    ] | None = None

    @model_validator(mode="after")
    def route_matches_kind(self) -> "ClassificationPayload":
        expected = {
            "IMPLEMENTATION_BUG": "ROUTE_A",
            "NEW_REQUIREMENT": "ROUTE_NEW_CHANGE",
            "ARCHIVED_PLANNING_BUG": "ROUTE_ARCHIVED_DELTA",
            "SPEC_BUG": "ROUTE_B",
            "SPEC_GAP": "ROUTE_B",
            "FALSE_POSITIVE": "ROUTE_C",
            "OUT_OF_SCOPE": "ROUTE_D",
            "INCONCLUSIVE": "BLOCKED",
        }[self.kind]
        if self.chosen_route is None:
            self.chosen_route = expected
        elif self.chosen_route != expected:
            raise ValueError(
                f"{self.kind} requires {expected}, got {self.chosen_route}"
            )
        if self.status == "BLOCKED" and self.kind != "INCONCLUSIVE":
            raise ValueError("BLOCKED classification must be INCONCLUSIVE")
        return self


class RedEvidence(StrictModel):
    status: Literal[
        "RED_CONFIRMED",
        "PASS_ALREADY",
        "TEST_NOT_REPRODUCING",
        "RED_WRONG_REASON",
        "TEST_SETUP_DEFECT",
        "BUG_NOT_TESTABLE",
        "BLOCKED",
    ]
    test_id: str | None = None
    test_path: str | None = None
    command: str | None = None
    expected_failure: str | None = None
    actual_failure: str | None = None
    failure_match: bool | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def red_requires_proof(self) -> "RedEvidence":
        if self.status == "RED_CONFIRMED":
            missing = [
                name
                for name in (
                    "test_id",
                    "command",
                    "expected_failure",
                    "actual_failure",
                )
                if not getattr(self, name)
            ]
            if missing or self.failure_match is not True:
                raise ValueError(
                    "RED_CONFIRMED requires matching failure and executed pytest evidence"
                )
        return self


FixExecutionRoute = Literal[
    "LOCAL_REPAIR",
    "VERIFY_APPLY",
    "APPLY_DESIGN",
    "APPLY_DESIGN_REVIEW",
    "APPLY_SCAFFOLD",
    "APPLY_RED",
    "APPLY_IMPLEMENT",
    "APPLY_GREEN",
    "FIX_BUG",
]


class FixEvidence(StrictModel):
    status: Literal["FIXED", "FAILED", "BLOCKED", "CANNOT_COMPLETE"]
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    execution_route: FixExecutionRoute = "LOCAL_REPAIR"
    patch_hash: str | None = None
    notes: str | None = None


class GreenEvidence(StrictModel):
    status: Literal["GREEN_CONFIRMED", "GREEN_FAILED", "BLOCKED"]
    test_id: str | None = None
    command: str | None = None
    targeted_regressions: list[str] = Field(default_factory=list)
    non_weaken_delta: str | None = None
    notes: str | None = None


class InvariantPayload(StrictModel):
    invariant_id: str = Field(min_length=1)
    plain_language_invariant: str = Field(min_length=1)
    technical_invariant: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    entry_conditions: list[str] = Field(default_factory=list)
    required_behavior: list[str] = Field(default_factory=list)
    forbidden_behavior: list[str] = Field(default_factory=list)
    known_writers: list[str] = Field(default_factory=list)
    known_readers: list[str] = Field(default_factory=list)
    proof_from_root_finding: str = Field(min_length=1)
    hunt_scope: list[str] = Field(default_factory=list)


class HuntCandidate(StrictModel):
    hunt_candidate_id: str = Field(min_length=1)
    evidence_paths: list[str] = Field(default_factory=list)
    observed_behavior: str = Field(min_length=1)
    possible_invariant_violation: str = Field(min_length=1)
    affected_component: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    location: str | None = None
    severity: Severity = "medium"


class HuntPayload(StrictModel):
    hunt_result: Literal["CLEAR", "CANDIDATES_FOUND", "INCONCLUSIVE"]
    candidates: list[HuntCandidate] = Field(default_factory=list)
    scope_checked: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def result_matches_candidates(self) -> "HuntPayload":
        if self.hunt_result == "CANDIDATES_FOUND" and not self.candidates:
            raise ValueError("CANDIDATES_FOUND requires candidates")
        if self.hunt_result == "CLEAR" and self.candidates:
            raise ValueError("CLEAR cannot carry candidates")
        return self


class RelationPayload(StrictModel):
    hunt_candidate_id: str
    relation: RelationType
    reason: str = Field(min_length=1)
    blocks_family_closure: bool
    linked_case_hint: str | None = None

    @model_validator(mode="after")
    def closure_rule(self) -> "RelationPayload":
        expected = self.relation in {
            "SAME_INVARIANT_FINDING",
            "INCONCLUSIVE",
        }
        if self.blocks_family_closure != expected:
            raise ValueError(
                f"{self.relation} blocks_family_closure must be {expected}"
            )
        return self


class FanInBranch(StrictModel):
    finding_id: str
    terminal_state: str
    red_status: str | None = None
    green_status: str | None = None
    patch_hash: str | None = None


class FanInPayload(StrictModel):
    status: Literal["FANIN_READY", "FANIN_INCOMPLETE"]
    root_bug: str
    root_cause_pattern: str
    error_class: str
    sibling_variants: list[str] = Field(default_factory=list)
    fix_patterns: list[str] = Field(default_factory=list)
    regression_test_pattern: str
    prevention_rule_candidate: str | None = None
    reusable_check_candidate: str | None = None
    remaining_exceptions: list[str] = Field(default_factory=list)
    blocking_findings: list[str] = Field(default_factory=list)
    branch_summary: list[FanInBranch] = Field(default_factory=list)


class OwnerQuestion(StrictModel):
    kind: Literal["PRODUCTION_GO", "FINAL_CLASS_VERDICT"]
    mission_id: str
    finding_id: str | None = None
    question: str
    options: list[str]


class OwnerDecision(StrictModel):
    approved: bool
    decision: str
    note: str | None = None


class FindingOutcome(StrictModel):
    finding: Finding
    classification: ClassificationPayload | None = None
    red: RedEvidence | None = None
    fix: FixEvidence | None = None
    green: GreenEvidence | None = None
    terminal_state: str
    invariant_id: str | None = None


class RepairState(StrictModel):
    mission_id: str
    repository_path: str
    workspace: str
    isolated_workspace: bool = False
    model: ModelSelection = Field(default_factory=ModelSelection)

    status: str = "READY"
    current_stage: str = "materialize"
    error: str | None = None

    queue: list[Finding] = Field(default_factory=list)
    active: Finding | None = None
    finished: list[FindingOutcome] = Field(default_factory=list)
    rejects: list[dict[str, Any]] = Field(default_factory=list)

    invariants: list[InvariantPayload] = Field(default_factory=list)
    hunts: list[HuntPayload] = Field(default_factory=list)
    relations: list[RelationPayload] = Field(default_factory=list)

    classification: ClassificationPayload | None = None
    red: RedEvidence | None = None
    red_test_content_identity: str | None = None
    fix: FixEvidence | None = None
    commit_status: str = "PENDING"
    commit_sha: str | None = None
    commit_subject: str | None = None
    commit_body: str | None = None
    commit_files: list[str] = Field(default_factory=list)
    green: GreenEvidence | None = None
    fanin: FanInPayload | None = None

    revision: int = 0
    runs: list[WorkerReceipt] = Field(default_factory=list)
    seen: list[str] = Field(default_factory=list)

    max_items: int = 1_000_000_000
    max_hunts: int = 1_000_000_000
    max_tokens: int = 1_000_000_000_000
    max_agent_input_tokens: int = DEFAULT_AGENT_INPUT_LIMIT
    learning: dict[str, Any] = Field(default_factory=dict)
    output_dir: str
    source_review: FindingReport | None = None
    source_report_path: str | None = None

    preflight: PreflightReport | None = None
    prepare_dependencies: bool = True
    prepare_integration: bool = False
    require_integration: bool = False

    # owner_go: авто GO на fix (owner_gate) и, если True, авто CLOSE на final_owner
    # без interrupt (типично nested verify-pbh). None — спрашивать Owner.
    owner_go: bool | None = None
    # owner_final: явный FINAL_CLASS_VERDICT; перекрывает owner_go auto-close.
    owner_final: str | None = None
    # Доп. инструкция для LLM commit message (переиспользуется один CommitAgent).
    commit_message_task: str | None = None
    hunt_rounds: int = 0
    # После CLOSE с FIXED: повторный hunt; стоп на CLEAR (см. can_post_complete_rehunt).
    max_search_rounds: int = 3
    search_round: int = 0

    @model_validator(mode="after")
    def active_not_queued_twice(self) -> "RepairState":
        ids = [finding.id for finding in self.queue]
        if len(ids) != len(set(ids)):
            raise ValueError("queue contains duplicate finding ids")
        return self
