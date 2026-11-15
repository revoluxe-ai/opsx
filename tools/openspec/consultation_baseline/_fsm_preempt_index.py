"""Индекс веток ``fsm._handle_cta`` до lookup ``CTA_TRANSITIONS`` @ BASE_SHA."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from tools.openspec.consultation_baseline._source_python import load_python_source

FSM_GIT_PATH = "src/stroy/channels/telegram/invest_yalta/fsm.py"


@dataclass(frozen=True)
class PreemptRule:
    """Одна ветка preempt в ``_handle_cta`` (строка ``if``)."""

    line_start: int
    line_end: int
    matcher: Callable[[str, str], bool]
    ref: str


def _handle_cta_body_lines(fsm_source: str) -> list[str]:
    start_idx = None
    end_idx = None
    for idx, line in enumerate(fsm_source.splitlines()):
        if line.startswith("def _handle_cta"):
            start_idx = idx
        if start_idx is not None and "key = (step, label)" in line:
            end_idx = idx
            break
    if start_idx is None or end_idx is None:
        raise ValueError("fsm._handle_cta body boundaries not found")
    return fsm_source.splitlines()[start_idx : end_idx + 1]


def _parse_state_label_if(
    line: str,
) -> tuple[str | None, set[str] | None, set[str] | None]:
    """Разобрать типовые ``if`` в теле ``_handle_cta``.

    Returns:
        (single_state, state_set, label_set) — ровно одно из полей может быть заполнено
        для state; label_set всегда может быть None для динамических веток.
    """

    m = re.search(
        r"if step == UxState\.(\w+) and label == \"([^\"]+)\"",
        line,
    )
    if m:
        return m.group(1), None, {m.group(2)}

    m = re.search(
        r"if step == UxState\.(\w+) and label in \{([^}]+)\}",
        line,
    )
    if m:
        labels = set(re.findall(r"\"([^\"]+)\"", m.group(2)))
        return m.group(1), None, labels

    m = re.search(r"if label == \"([^\"]+)\"", line)
    if m:
        return None, None, {m.group(1)}

    m = re.search(r"if label in \{([^}]+)\}", line)
    if m:
        labels = set(re.findall(r"\"([^\"]+)\"", m.group(1)))
        return None, None, labels

    m = re.search(
        r"if step in \{([^}]+)\} and label == \"([^\"]+)\"",
        line,
    )
    if m:
        states = set(re.findall(r"UxState\.(\w+)", m.group(1)))
        return None, states, {m.group(2)}

    m = re.search(
        r"if label == \"([^\"]+)\" and step in \{([^}]+)\}",
        line,
    )
    if m:
        states = set(re.findall(r"UxState\.(\w+)", m.group(2)))
        return None, states, {m.group(1)}

    m = re.search(
        r"if step == UxState\.(\w+) and is_goal_option_label\(label\)",
        line,
    )
    if m:
        return m.group(1), None, None

    m = re.search(
        r"if step == UxState\.(\w+) and label in _EXPERIENCE_PRESETS",
        line,
    )
    if m:
        return m.group(1), None, None

    m = re.search(
        r"if step == UxState\.platform and label in platform_choices_for_ui\(\)",
        line,
    )
    if m:
        return "platform", None, None

    if "if label in OFF_PATH_BUTTONS" in line:
        return None, None, None

    m = re.search(
        r"if step in \{([^}]+)\} and label in _BUDGET_SKIP_LABELS",
        line,
    )
    if m:
        states = set(re.findall(r"UxState\.(\w+)", m.group(1)))
        return None, states, None

    m = re.search(
        r"if step == UxState\.currency and label in _CURRENCY_SUFFIX",
        line,
    )
    if m:
        return "currency", None, None

    if "_tz_preset(label) if step == UxState.tz" in line:
        return "tz", None, None

    if "snapshot.active_booking_id and label in" in line:
        return None, None, set(re.findall(r"\"([^\"]+)\"", line))

    return None, None, None


def _make_matcher(
    single_state: str | None,
    state_set: set[str] | None,
    label_set: set[str] | None,
    dynamic: Callable[[str, str], bool] | None,
) -> Callable[[str, str], bool]:
    def _match(state: str, label: str) -> bool:
        if dynamic is not None and dynamic(state, label):
            return True
        if label_set is not None and label not in label_set:
            return False
        if single_state is not None and state != single_state:
            return False
        if state_set is not None and state not in state_set:
            return False
        if label_set is None and single_state is None and state_set is None and dynamic is None:
            return False
        return label_set is not None or single_state is not None or state_set is not None

    return _match


def build_preempt_rules(
    repo_root,
    base_sha: str,
    dynamics: object | None = None,
) -> list[PreemptRule]:
    """Построить список preempt-правил из ``fsm.py`` @ ``base_sha``.

    Args:
        repo_root: корень репозитория.
        base_sha: git pin.
        dynamics: опциональный объект с динамическими предикатами (goal, presets, …).
    """

    fsm_source = load_python_source(repo_root, base_sha, FSM_GIT_PATH)
    body_lines = _handle_cta_body_lines(fsm_source)
    rules: list[PreemptRule] = []

    dyn_goal = getattr(dynamics, "is_goal_option_label", None) if dynamics else None
    dyn_exp = getattr(dynamics, "experience_presets", None) if dynamics else None
    dyn_platform = getattr(dynamics, "platform_labels", None) if dynamics else None
    dyn_off_path = getattr(dynamics, "off_path_buttons", None) if dynamics else None
    dyn_budget_skip = getattr(dynamics, "budget_skip_labels", None) if dynamics else None
    dyn_currency = getattr(dynamics, "currency_suffix_labels", None) if dynamics else None
    dyn_tz = getattr(dynamics, "tz_preset_matches", None) if dynamics else None
    dyn_active_booking = getattr(dynamics, "active_booking_cta_labels", None) if dynamics else None

    for line_no, line in enumerate(body_lines, start=1):
        stripped = line.strip()
        if not stripped.startswith("if "):
            continue
        single_state, state_set, label_set = _parse_state_label_if(line)
        dynamic_fn: Callable[[str, str], bool] | None = None

        if "is_goal_option_label" in line and dyn_goal is not None:
            state = single_state or "goal"

            def _goal_matcher(s: str, lbl: str, st: str = state) -> bool:
                return s == st and bool(dyn_goal(lbl))

            dynamic_fn = _goal_matcher
        elif "_EXPERIENCE_PRESETS" in line and dyn_exp is not None:

            def _exp_matcher(s: str, lbl: str) -> bool:
                return s == "experience" and lbl in dyn_exp

            dynamic_fn = _exp_matcher
        elif "platform_choices_for_ui" in line and dyn_platform is not None:

            def _plat_matcher(s: str, lbl: str) -> bool:
                return s == "platform" and lbl in dyn_platform

            dynamic_fn = _plat_matcher
        elif "OFF_PATH_BUTTONS" in line and dyn_off_path is not None:

            def _off_matcher(_s: str, lbl: str) -> bool:
                return lbl in dyn_off_path

            dynamic_fn = _off_matcher
        elif "_BUDGET_SKIP_LABELS" in line and dyn_budget_skip is not None:

            def _budget_matcher(s: str, lbl: str) -> bool:
                return s in {"budgetUnknown", "budgetClarify"} and lbl in dyn_budget_skip

            dynamic_fn = _budget_matcher
        elif "_CURRENCY_SUFFIX" in line and dyn_currency is not None:

            def _cur_matcher(s: str, lbl: str) -> bool:
                return s == "currency" and lbl in dyn_currency

            dynamic_fn = _cur_matcher
        elif "_tz_preset" in line and dyn_tz is not None:

            def _tz_matcher(s: str, lbl: str) -> bool:
                return s == "tz" and bool(dyn_tz(lbl))

            dynamic_fn = _tz_matcher
        elif "snapshot.active_booking_id" in line and dyn_active_booking is not None:

            def _ab_matcher(_s: str, lbl: str) -> bool:
                return lbl in dyn_active_booking

            dynamic_fn = _ab_matcher

        if (
            single_state is None
            and state_set is None
            and label_set is None
            and dynamic_fn is None
        ):
            continue

        matcher = _make_matcher(single_state, state_set, label_set, dynamic_fn)
        all_lines = fsm_source.splitlines()
        body_start = all_lines.index(body_lines[0])
        abs_line = body_start + line_no
        ref = f"fsm._handle_cta:L{abs_line}"
        rules.append(
            PreemptRule(
                line_start=abs_line,
                line_end=abs_line,
                matcher=matcher,
                ref=ref,
            )
        )

    return rules


def first_matching_preempt(
    state: str,
    label: str,
    rules: list[PreemptRule],
) -> PreemptRule | None:
    """Первое совпавшее preempt-правило (порядок = порядок ``if`` в FSM)."""

    for rule in rules:
        if rule.matcher(state, label):
            return rule
    return None
