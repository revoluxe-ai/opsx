"""Проверка documentation evidence (SOURCE_INSPECTED → COMPATIBILITY_VERIFIED).

MCP/URL — только механизм захвата; PASS требует digest-bound bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DocFetchResult:
    """Результат захвата документации."""

    ok: bool
    content: bytes = b""
    code: str = ""
    detail: str = ""


class DocumentFetcher(Protocol):
    """Инъектируемый fetcher (fixtures в тестах, MCP в runtime)."""

    def fetch(self, url: str) -> DocFetchResult:
        """Загрузить документ по URL или вернуть ошибку."""


class McpUnavailableError(Exception):
    """MCP недоступен — не трактуется как успешная верификация."""


class FixtureDocumentFetcher:
    """Локальные URL → bytes для CI без сети."""

    def __init__(self, mapping: dict[str, bytes]) -> None:
        self._mapping = mapping

    def fetch(self, url: str) -> DocFetchResult:
        if url not in self._mapping:
            return DocFetchResult(ok=False, code="MCP_DOC_UNAVAILABLE", detail=url)
        content = self._mapping[url]
        return DocFetchResult(ok=True, content=content)


@dataclass
class DocFinding:
    code: str
    detail: str
    claim_id: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_relative_doc_path(repo_root: Path, path_str: str) -> Path | None:
    if not path_str or path_str.startswith("http"):
        return None
    candidate = (repo_root / path_str).resolve()
    if candidate.is_file():
        try:
            candidate.relative_to(repo_root.resolve())
        except ValueError:
            return None
        return candidate
    return None


def verify_integrated_documentation_evidence(
    receipt: dict[str, Any],
    *,
    repo_root: Path,
    fetcher: DocumentFetcher,
) -> list[DocFinding]:
    """Integrated gate: A acquisition → B capture → C compatibility (S2)."""

    findings: list[DocFinding] = []
    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        findings.extend(verify_claim_sources(claim, fetcher=fetcher, repo_root=repo_root))
    return findings


def verify_claim_sources(
    claim: dict[str, Any],
    *,
    fetcher: DocumentFetcher | None,
    repo_root: Path | None = None,
) -> list[DocFinding]:
    """Проверить, что PASS-claim не опирается на неинспектированные URL/MCP."""

    findings: list[DocFinding] = []
    if claim.get("result") != "PASS":
        return findings
    cid = claim.get("claim_id")
    sources = claim.get("sources") or []
    if not sources:
        return findings

    for source in sources:
        if not isinstance(source, dict):
            continue
        kind = source.get("kind")
        if kind == "mcp_discovery":
            findings.append(
                DocFinding(
                    "MCP_NOT_VERIFICATION_AUTHORITY",
                    "mcp_discovery cannot establish compatibility PASS alone",
                    cid,
                )
            )
        if kind == "authoritative_doc_digest":
            path = source.get("path")
            digest = source.get("content_sha256")
            revision = source.get("revision") or ""
            url = revision if revision.startswith("http") else (path if path and str(path).startswith("http") else "")
            snapshot_url = revision if revision.startswith("snapshot://") else ""

            if url and not digest:
                findings.append(
                    DocFinding(
                        "URL_WITHOUT_INSPECTED_SOURCE",
                        "HTTP documentation claim without content_sha256",
                        cid,
                    )
                )
                continue

            if repo_root is not None and path and not str(path).startswith("http"):
                local = _repo_relative_doc_path(repo_root, str(path))
                if local and digest:
                    actual = sha256_bytes(local.read_bytes())
                    if actual != digest:
                        findings.append(
                            DocFinding(
                                "LIBRARY_API_INCOMPATIBLE",
                                "offline documentation snapshot digest mismatch",
                                cid,
                            )
                        )
                    continue

            if snapshot_url and fetcher is not None:
                fetched = fetcher.fetch(snapshot_url)
                if not fetched.ok:
                    findings.append(
                        DocFinding(
                            fetched.code or "DOC_ACQUISITION_UNAVAILABLE",
                            fetched.detail or snapshot_url,
                            cid,
                        )
                    )
                    continue
                declared = source.get("content_sha256")
                actual = sha256_bytes(fetched.content)
                if declared and declared != actual:
                    findings.append(
                        DocFinding(
                            "LIBRARY_API_INCOMPATIBLE",
                            "documentation digest mismatch (wrong version/doc)",
                            cid,
                        )
                    )
                continue

            if url:
                if fetcher is None:
                    findings.append(
                        DocFinding(
                            "DOC_ACQUISITION_UNAVAILABLE",
                            "HTTP authoritative_doc_digest requires acquisition in integrated gate",
                            cid,
                        )
                    )
                    continue
                try:
                    fetched = fetcher.fetch(url)
                except McpUnavailableError:
                    findings.append(
                        DocFinding(
                            "MCP_DOC_UNAVAILABLE",
                            "documentation fetch unavailable; cannot verify",
                            cid,
                        )
                    )
                    continue
                if not fetched.ok:
                    findings.append(
                        DocFinding(
                            fetched.code or "DOC_ACQUISITION_UNAVAILABLE",
                            fetched.detail or url,
                            cid,
                        )
                    )
                    continue
                declared = source.get("content_sha256")
                actual = sha256_bytes(fetched.content)
                if declared and declared != actual:
                    findings.append(
                        DocFinding(
                            "LIBRARY_API_INCOMPATIBLE",
                            "documentation digest mismatch (wrong version/doc)",
                            cid,
                        )
                    )
    return findings
