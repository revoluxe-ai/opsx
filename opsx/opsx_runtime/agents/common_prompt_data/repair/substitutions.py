"""Строки, одинаково подставляемые в prompt у нескольких repair-агентов.

Только данные — без сборки prompt и без таблицы ролей.
"""

OPSX_ORCHESTRATION_PATH = ".cursor/pipeline/ORCHESTRATION_MVP.md"

BUG_TEAM_POLICY_PATH = (
    ".cursor/pipeline/bug-team/policy/BUG_FAMILY_FANOUT_POLICY.json"
)

REPAIR_AGENT_SHARED_RULES = """\
- Work only in the workspace shown above.
- Never git commit, push, deploy, or mutate external production systems.
- Never forge Owner approval.
- Obey the role write boundary.
- Do not silently weaken a RED regression test to obtain GREEN.
- INPUT CONTEXT may include preflight — treat it as the environment authority.
- For pytest, use preflight.test_runtime.pytest_prefix exactly when present.
- If your required test lane depends on integration capability that preflight marks unavailable, return a BLOCKED status in your answer schema instead of fabricating evidence."""
