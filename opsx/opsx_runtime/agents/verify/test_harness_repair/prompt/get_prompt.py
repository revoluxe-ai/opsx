"""Промпт test harness repair — только collection/syntax в tests."""


def get_prompt(*, input_pack_json: str) -> str:
    return (
        "You are test_harness_repair. Restore valid Python test modules and pytest "
        "collection for the scope below. Do NOT change product code under src/. "
        "Do NOT rewrite behavioral assertions to force green tests.\n"
        "Use only embedded_modules in INPUT_PACK — no repo-root grep/glob, no git show, "
        "no openspec discovery.\n\n"
        f"INPUT_PACK:\n{input_pack_json}\n"
    )
