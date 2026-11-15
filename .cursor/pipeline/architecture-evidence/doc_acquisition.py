"""Source acquisition adapters для integrated documentation verification (S2)."""

from __future__ import annotations

from pathlib import Path

from doc_verification import DocFetchResult, DocumentFetcher


class UnavailableAcquisitionFetcher:
    """Integrated gate: live MCP не подключён — HTTP acquisition fail-closed."""

    def fetch(self, url: str) -> DocFetchResult:
        return DocFetchResult(
            ok=False,
            code="DOC_ACQUISITION_UNAVAILABLE",
            detail=f"no supported live acquisition for {url} in S2 integrated gate",
        )


class FilesystemSnapshotFetcher:
    """Offline capture: читать repo-relative snapshot bytes по URL-key mapping."""

    def __init__(self, mapping: dict[str, Path]) -> None:
        self._mapping = mapping

    def fetch(self, url: str) -> DocFetchResult:
        path = self._mapping.get(url)
        if path is None or not path.is_file():
            return DocFetchResult(
                ok=False,
                code="DOC_ACQUISITION_UNAVAILABLE",
                detail=url,
            )
        return DocFetchResult(ok=True, content=path.read_bytes())


def build_integrated_document_fetcher(
    repo_root: Path,
    change_root: Path | None = None,
) -> DocumentFetcher:
    """Fetcher для planning gate: offline snapshots only (S2)."""

    snapshot_dir = (
        repo_root
        / ".cursor/pipeline/architecture-evidence-tests/fixtures/authoritative-docs"
    )
    mapping: dict[str, Path] = {}
    if snapshot_dir.is_dir():
        for path in sorted(snapshot_dir.glob("*.md")):
            mapping[f"snapshot://{path.name}"] = path
    return _ChainedFetcher(
        FilesystemSnapshotFetcher(mapping),
        UnavailableAcquisitionFetcher(),
    )


class _ChainedFetcher:
    def __init__(self, *fetchers: DocumentFetcher) -> None:
        self._fetchers = fetchers

    def fetch(self, url: str) -> DocFetchResult:
        if url.startswith("snapshot://"):
            return self._fetchers[0].fetch(url)
        return self._fetchers[-1].fetch(url)
