"""Structured canonical /opsx-static-check FULL result for prep-green-scope."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StaticProblem:
    """Один actionable элемент из PROBLEMS (STATIC bucket)."""

    path: str
    fix_target: str = ""


@dataclass(frozen=True)
class TestProblem:
    """Один actionable pytest node id из PROBLEMS (TESTS bucket)."""

    node: str
    fix_target: str = ""


@dataclass
class CheckPacket:
    """Terminal classification одной проверки из CHECKS."""

    check_id: str
    status: str
    notes: str = ""
    executed_count: int | None = None
    static_problems: list[StaticProblem] = field(default_factory=list)
    test_problems: list[TestProblem] = field(default_factory=list)


@dataclass
class FullStaticCheckResult:
    """Fan-in static-check FULL (machine handoff → prep-green)."""

    status: str
    mode: str
    fingerprint: str
    checks: list[CheckPacket]
    static_check_identity: str = ""
    static_check_config_identity: str = ""
    input_snapshot_identity: str = ""
    probe_execution_identity: str = ""
    execution_id: str = ""
    handoff_complete: bool = True
    handoff_incomplete_reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FullStaticCheckResult:
        """Разбор canonical handoff payload (not human Final report)."""

        checks_raw = data.get("checks") or []
        checks: list[CheckPacket] = []
        for item in checks_raw:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("check_id") or "")
            st = str(item.get("status") or "")
            notes = str(item.get("notes") or "")
            executed = item.get("executed_count")
            exec_n = int(executed) if executed is not None else None
            static_p: list[StaticProblem] = []
            for sp in item.get("static_problems") or []:
                if isinstance(sp, dict) and sp.get("path"):
                    static_p.append(
                        StaticProblem(
                            path=str(sp["path"]),
                            fix_target=str(sp.get("fix_target") or ""),
                        )
                    )
            test_p: list[TestProblem] = []
            for tp in item.get("test_problems") or []:
                if isinstance(tp, dict) and tp.get("node"):
                    test_p.append(
                        TestProblem(
                            node=str(tp["node"]),
                            fix_target=str(tp.get("fix_target") or ""),
                        )
                    )
            checks.append(
                CheckPacket(
                    check_id=cid,
                    status=st,
                    notes=notes,
                    executed_count=exec_n,
                    static_problems=static_p,
                    test_problems=test_p,
                )
            )
        return cls(
            status=str(data.get("status") or ""),
            mode=str(data.get("mode") or "FULL"),
            fingerprint=str(data.get("fingerprint") or ""),
            checks=checks,
            static_check_identity=str(
                data.get("static_check_config_identity")
                or data.get("static_check_identity")
                or ""
            ),
            static_check_config_identity=str(
                data.get("static_check_config_identity")
                or data.get("static_check_identity")
                or ""
            ),
            input_snapshot_identity=str(data.get("input_snapshot_identity") or ""),
            probe_execution_identity=str(data.get("probe_execution_identity") or ""),
            execution_id=str(data.get("execution_id") or ""),
            handoff_complete=bool(data.get("handoff_complete", True)),
            handoff_incomplete_reasons=list(data.get("handoff_incomplete_reasons") or []),
        )

    @classmethod
    def from_static_check_handoff(cls, handoff: dict[str, Any]) -> FullStaticCheckResult:
        """Parse canonical ``prep_green_full_handoff`` artifact only."""

        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]
        contracts = repo / ".cursor" / "static-check" / "contracts"
        if str(contracts) not in sys.path:
            sys.path.insert(0, str(contracts))
        import prep_green_full_handoff as pgfh  # noqa: WPS433

        parsed = pgfh.parse_handoff(handoff)
        return cls.from_dict(parsed)

    def check(self, check_id: str) -> CheckPacket | None:
        for c in self.checks:
            if c.check_id == check_id:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        """Сериализация для convergence evidence chain."""

        return {
            "status": self.status,
            "mode": self.mode,
            "fingerprint": self.fingerprint,
            "static_check_identity": self.static_check_identity,
            "static_check_config_identity": self.static_check_config_identity,
            "input_snapshot_identity": self.input_snapshot_identity,
            "probe_execution_identity": self.probe_execution_identity,
            "checks": [
                {
                    "check_id": c.check_id,
                    "status": c.status,
                    "notes": c.notes,
                    "executed_count": c.executed_count,
                    "static_problems": [
                        {"path": p.path, "fix_target": p.fix_target}
                        for p in c.static_problems
                    ],
                    "test_problems": [
                        {"node": p.node, "fix_target": p.fix_target}
                        for p in c.test_problems
                    ],
                }
                for c in self.checks
            ],
        }
