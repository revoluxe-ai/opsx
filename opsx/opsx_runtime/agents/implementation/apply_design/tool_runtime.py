"""Async handlers custom tools apply-design (без привязки к провайдеру адаптера в core)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from agents.capabilities.manifest import AgentCapabilityManifest
from agents.capabilities.path_policy import (
    domain_roots,
    path_denied,
    rel_posix,
)
from agents.implementation.apply_design.capability import file_digest_bytes
from agents.implementation.apply_design.read_source import execute_read_source
from agents.implementation.apply_design.submit_content_resolver import (
    resolve_submit_apply_design_content,
)
from workflows.implementation.apply_design_hop_receipt import (
    DesignSubmissionRecord,
    remember_design_submission,
)
from workflows.implementation.design_machine_validation import (
    apply_design_acceptance_field_gaps,
    evaluate_apply_design_snapshot_from_body_async,
    validate_apply_design_at_path_async,
)

_MAX_SEARCH_HITS = 200
_MAX_FILE_BYTES = 2_000_000
_MAX_GATE_ERRORS = 40


@dataclass(frozen=True, slots=True)
class DesignPublishOutcome:
    """Результат staging publish: канон меняется только при ``ok``."""

    ok: bool
    unchanged: bool
    sha256: str | None
    code: str | None
    errors: list[dict[str, Any]] | None = None


class ApplyDesignToolRuntime:
    """Замыкание manifest + repo для трёх custom tools."""

    def __init__(self, manifest: AgentCapabilityManifest) -> None:
        self._manifest = manifest
        self._repo = manifest.repo_root.resolve()
        self.last_submission: DesignSubmissionRecord | None = None
        self._accepted_submission: DesignSubmissionRecord | None = None
        self._revision_digest = self._read_revision_digest()

    def _note_submission(self, record: DesignSubmissionRecord) -> None:
        accepted, hop = remember_design_submission(self._accepted_submission, record)
        self._accepted_submission = accepted
        self.last_submission = hop

    def _reject(
        self,
        code: str,
        *,
        field: str | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        self._note_submission(
            DesignSubmissionRecord(
                ok=False,
                unchanged=False,
                sha256=None,
                code=code,
                message=message,
            )
        )
        out: dict[str, Any] = {"ok": False, "code": code}
        if field:
            out["field"] = field
        if message:
            out["message"] = message
        return out

    def current_revision_digest(self) -> str:
        """CAS-токен текущей ревизии APPLY-DESIGN (обновляется после publish)."""
        return self._revision_digest

    def _read_revision_digest(self) -> str:
        sub = self._manifest.design_submission
        if sub is None or not sub.json_path.is_file():
            return ""
        return file_digest_bytes(sub.json_path.read_bytes())

    async def search_repository(
        self,
        args: Mapping[str, Any],
        _ctx: Any,
    ) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        domains_raw = args.get("domains")
        if not query or not isinstance(domains_raw, list) or not domains_raw:
            return {"ok": False, "code": "INVALID_ARGS"}
        domains = [str(d).strip() for d in domains_raw if str(d).strip()]
        allowed = set(self._manifest.read_domains)
        if any(d not in allowed for d in domains):
            return {"ok": False, "code": "UNKNOWN_DOMAIN"}
        hits: list[dict[str, str]] = []
        q_lower = query.lower()
        for domain in domains:
            if domain == "prior_designs":
                for desc in self._manifest.prior_designs.values():
                    if q_lower in desc.summary.lower() or q_lower in desc.change_id.lower():
                        hits.append(
                            {
                                "source_id": desc.source_id,
                                "line": "0",
                                "text": desc.summary,
                            }
                        )
                continue
            await self._search_domain(domain, q_lower, hits)
            if len(hits) >= _MAX_SEARCH_HITS:
                break
        return {"ok": True, "hits": hits[:_MAX_SEARCH_HITS]}

    async def read_source(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        return await execute_read_source(self._manifest, args)

    async def submit_apply_design(
        self,
        args: Mapping[str, Any],
        _ctx: Any,
    ) -> dict[str, Any]:
        sub = self._manifest.design_submission
        if sub is None:
            return {"ok": False, "code": "NO_SUBMISSION_TARGET"}
        content = args.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        if not isinstance(content, str) or not content.strip():
            return self._reject(
                "INVALID_ARGS",
                field="content",
                message=(
                    "content must be a JSON string or object. "
                    "Patch the canon with $from_path set to the canonical "
                    "APPLY-DESIGN.json path; extra keys are deep-merged. "
                    "Do not set artifact_identity.artifact_sha256 — review binds "
                    "that digest from file bytes."
                ),
            )
        base_digest = str(args.get("base_digest") or "")
        if base_digest != self._revision_digest:
            self._note_submission(
                DesignSubmissionRecord(
                    ok=False,
                    unchanged=False,
                    sha256=None,
                    code="DESIGN_DIGEST_CONFLICT",
                )
            )
            return {"ok": False, "code": "DESIGN_DIGEST_CONFLICT"}
        target = sub.json_path
        data = content.encode("utf-8")
        if target.is_file():
            existing = await asyncio.to_thread(target.read_bytes)
            if file_digest_bytes(existing) == file_digest_bytes(data):
                digest = file_digest_bytes(existing)
                self._note_submission(
                    DesignSubmissionRecord(
                        ok=True, unchanged=True, sha256=digest, code=None
                    )
                )
                return {
                    "ok": True,
                    "unchanged": True,
                    "revision_digest": digest,
                }
            on_disk = file_digest_bytes(existing)
            if on_disk != self._revision_digest:
                self._note_submission(
                    DesignSubmissionRecord(
                        ok=False,
                        unchanged=False,
                        sha256=None,
                        code="DESIGN_DIGEST_CONFLICT",
                    )
                )
                return {"ok": False, "code": "DESIGN_DIGEST_CONFLICT"}
        resolved = await resolve_submit_apply_design_content(
            content,
            repo=self._repo,
            target=target,
        )
        if not resolved.ok or resolved.content is None or resolved.body is None:
            code = resolved.code or "INVALID_JSON"
            return self._reject(code, field=resolved.field, message=resolved.message)
        content = resolved.content
        body = resolved.body
        gaps = apply_design_acceptance_field_gaps(body)
        if gaps:
            self._note_submission(
                DesignSubmissionRecord(
                    ok=False,
                    unchanged=False,
                    sha256=None,
                    code="DESIGN_INCOMPLETE",
                )
            )
            return {
                "ok": False,
                "code": "DESIGN_INCOMPLETE",
                "missing_fields": gaps,
            }
        publish = await self._publish_validated(sub, target, content, body)
        self._note_submission(
            DesignSubmissionRecord(
                ok=publish.ok,
                unchanged=publish.unchanged,
                sha256=publish.sha256,
                code=publish.code,
            )
        )
        if not publish.ok:
            out: dict[str, Any] = {
                "ok": False,
                "code": publish.code or "DESIGN_VALIDATION_FAILED",
            }
            if publish.errors:
                out["errors"] = publish.errors
            return out
        self._revision_digest = publish.sha256 or self._revision_digest
        return {
            "ok": True,
            "unchanged": publish.unchanged,
            "revision_digest": publish.sha256,
        }

    async def _publish_validated(
        self,
        sub: Any,
        target: Path,
        content: str,
        body: dict[str, Any],
    ) -> DesignPublishOutcome:
        """Staging: schema/semantics/snapshot; CAS publish только после PASS."""
        parent = target.parent
        await asyncio.to_thread(parent.mkdir, parents=True, exist_ok=True)
        fd, staging_name = tempfile.mkstemp(
            dir=str(parent),
            prefix=".apply-design-staging-",
            suffix=".json",
        )
        staging_path = Path(staging_name)
        try:
            await asyncio.to_thread(os.write, fd, content.encode("utf-8"))
            await asyncio.to_thread(os.close, fd)
            fd = -1
            gate = await validate_apply_design_at_path_async(
                staging_path,
                repo_root=self._repo,
            )
            if not gate.ok:
                capped = list(gate.errors[:_MAX_GATE_ERRORS])
                return DesignPublishOutcome(
                    ok=False,
                    unchanged=False,
                    sha256=None,
                    code="DESIGN_MACHINE_GATE_FAILED",
                    errors=capped,
                )
            content = await asyncio.to_thread(
                staging_path.read_text,
                encoding="utf-8",
            )
            body = json.loads(content)
            snapshot = await evaluate_apply_design_snapshot_from_body_async(
                body,
                repo_root=self._repo,
            )
            if not snapshot.ok:
                code = snapshot.code or "STALE_APPLY_DESIGN"
                return DesignPublishOutcome(
                    ok=False,
                    unchanged=False,
                    sha256=None,
                    code=code,
                )
            if target.is_file():
                existing = await asyncio.to_thread(target.read_bytes)
                on_disk = file_digest_bytes(existing)
                if on_disk != self._revision_digest:
                    return DesignPublishOutcome(
                        ok=False,
                        unchanged=False,
                        sha256=None,
                        code="DESIGN_DIGEST_CONFLICT",
                    )
            fd2, tmp_name = tempfile.mkstemp(
                dir=str(parent),
                prefix=".apply-design-",
                suffix=".tmp",
            )
            try:
                data = content.encode("utf-8")
                await asyncio.to_thread(os.write, fd2, data)
                await asyncio.to_thread(os.close, fd2)
                fd2 = -1
                await asyncio.to_thread(os.replace, tmp_name, str(target))
                written = file_digest_bytes(data)
                return DesignPublishOutcome(
                    ok=True,
                    unchanged=False,
                    sha256=written,
                    code=None,
                )
            finally:
                if fd2 >= 0:
                    await asyncio.to_thread(os.close, fd2)
                if os.path.exists(tmp_name):
                    await asyncio.to_thread(os.unlink, tmp_name)
        finally:
            if fd >= 0:
                await asyncio.to_thread(os.close, fd)
            if staging_path.exists():
                await asyncio.to_thread(os.unlink, staging_path)

    async def _search_domain(
        self,
        domain: str,
        query_lower: str,
        hits: list[dict[str, str]],
    ) -> None:
        for base in domain_roots(domain, self._repo):
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    rel = rel_posix(path, self._repo)
                except ValueError:
                    continue
                if path_denied(rel):
                    continue
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                try:
                    text = await asyncio.to_thread(
                        path.read_text,
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError:
                    continue
                sid = f"{domain}:{rel}"
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if query_lower in line.lower():
                        hits.append(
                            {
                                "source_id": sid,
                                "line": str(lineno),
                                "text": line.strip(),
                            }
                        )
                        if len(hits) >= _MAX_SEARCH_HITS:
                            return
