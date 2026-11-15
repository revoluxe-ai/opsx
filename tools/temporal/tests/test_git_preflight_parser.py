from parsers import parse_bugbot_result


def test_preflight_ok_ignores_shell_narrative_when_table_present():
    raw = """Shell недоступен из‑за сломанного хука, поэтому ревью идёт по файлам.

REVIEW_SCOPE: FULL_BRANCH_DIFF

Bugbot found 1 findings

| Severity | Location | Finding |
| MEDIUM | `src/store/index.js:464` | Currency clawback bug |
"""
    without_preflight = parse_bugbot_result(raw, git_preflight_ok=False)
    with_preflight = parse_bugbot_result(raw, git_preflight_ok=True)

    assert without_preflight.completed is False
    assert with_preflight.completed is True
    assert with_preflight.clean is False
    assert len(with_preflight.repairable_findings) == 1


def test_preflight_ok_accepts_opsx_only_as_product_clean():
    raw = """REVIEW_SCOPE: FULL_BRANCH_DIFF

Bugbot found 2 findings

| Severity | Location | Finding |
| High | `opsx/tools/lib/a.mjs:10` | Process bug |
| High | `opsx/tools/lib/b.mjs:20` | Process bug |
"""
    parsed = parse_bugbot_result(raw, git_preflight_ok=True)
    assert parsed.completed is True
    assert parsed.clean is True
    assert len(parsed.repairable_findings) == 0
