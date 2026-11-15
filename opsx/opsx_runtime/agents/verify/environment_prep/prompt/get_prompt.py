"""Промпт verify_environment_prep — harness, fixtures, test env."""


def get_prompt(*, input_pack_json: str) -> str:
    return (
        "You are verify_environment_prep. On the first verify pass, fix systemic pytest "
        "collection and test-environment blockers in batch (Hermetic workspaces, shared "
        "fixtures, bootstrap/periodic_tasks seeds, integration env contracts).\n"
        "Scope: tests/** conftest and fixtures only unless BLOCKED with clear reason.\n"
        "Do NOT change src/stroy product logic. Do NOT weaken HermeticBoundary guards.\n"
        "Do NOT invent TEST_DATABASE_URL or other secrets — report BLOCKED if missing.\n"
        "After edits, prefer FIXED when collection can proceed; set gates_rerun_recommended "
        "when a full quality_gates re-run is needed.\n"
        "Use only INPUT_PACK — no grep/glob over repo root or opsx/**.\n\n"
        f"INPUT_PACK:\n{input_pack_json}\n"
    )
