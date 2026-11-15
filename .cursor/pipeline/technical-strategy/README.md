# Technical Strategy helper

Target-native publisher for `/opsx-ts-design` and `/opsx-ts-review`.

`design` writes only `<changeRoot>/ts/TS-DESIGN.md` and `TS-DESIGN.json`.
The artifact is bound to the current owner-contract, proposal, and specs.
A payload that claims a different `upstream_provenance` fails with
`INPUT_REVISION_STALE` and writes nothing. `freshness` is read-only.
`planning_risk_sha256` is rejected. `design.md` stays with the existing
design writers. The design action cannot pass `--reviewer-id` or `--findings`.

`review` writes only `<changeRoot>/ts/TS-REVIEW.md` and `TS-REVIEW.json`.
It requires `--reviewer-id` and a separate evidence file whose
`independence` is `INDEPENDENT_OF_TS_DESIGN_WRITER`. A bare `review` call,
a design-writer id, or an evidence file that claims `review_state` fails
closed and writes nothing. `PASS` requires zero findings, a current
TS-DESIGN hash, and the live source input revision. A stale design is
`BLOCKED` / `INPUT_REVISION_STALE`. Evidence bound to another design hash
is `BLOCKED` / `CROSS_REVISION_REUSE`. `PASS` does not unlock `design.md`.
