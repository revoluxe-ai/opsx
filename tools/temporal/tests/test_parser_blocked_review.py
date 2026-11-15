from parsers import is_transient_error, parse_bugbot_result


def test_blocked_hook_review_is_not_clean():
    raw = """Bugbot review against `stage` **не стартовал**: все tool calls (Shell/Read/Grep/Task) режет сломанный `preToolUse` hook.

Итог: таблица Severity | Location (file:line) | Finding
(или «no bugs» / «no diff»). Ничего не чини."""
    parsed = parse_bugbot_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.actionable_findings == []


def test_degraded_working_tree_review_is_not_complete():
    raw = """Shell is blocked by a broken hook, so this is a working-tree review of runtime-core / `opsx/tools`, not a full `git diff`.

Bugbot found 4 findings

| Severity | Location | Finding |
| High | opsx/tools/lib/a.mjs:10 | Example |
"""
    parsed = parse_bugbot_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None


def test_explicit_clean_requires_review_scope():
    parsed = parse_bugbot_result("Bugbot found no bugs.")
    assert parsed.completed is False
    assert parsed.clean is None


def test_product_timeout_mention_is_not_transient():
    raw = """REVIEW_SCOPE: FULL_BRANCH_DIFF
| Severity | Location | Finding |
| Medium | `src/Foo.vue:10` | Example finding |

**Not flagged:** timeout/fail-closed in `softProdNovelSave.js`, semantic reload gate."""
    assert is_transient_error(raw) is False
    parsed = parse_bugbot_result(raw, git_preflight_ok=True)
    assert parsed.transient_error is False
    assert parsed.completed is True


def test_real_timeout_error_is_transient():
    raw = "CLI connection timeout while waiting for agent response."
    assert is_transient_error(raw) is True


def test_corrupt_agent_checkpoint_is_transient():
    raw = (
        "internal: Corrupt local agent checkpoint: missing root blob "
        "abc for agent agent-deadbeef"
    )
    assert is_transient_error(raw) is True


def test_explicit_clean_with_scope_is_detected():
    raw = """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 0

Bugbot found no bugs."""
    parsed = parse_bugbot_result(raw)
    assert parsed.completed is True
    assert parsed.clean is True
