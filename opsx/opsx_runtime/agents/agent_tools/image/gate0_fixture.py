"""Контрольная картинка Gate 0: деталь только в PNG, не в тексте задания."""

from __future__ import annotations

from pathlib import Path

# Намеренно не класть в copy_text / rubric промпта исполнителя.
GATE0_VISUAL_SECRET = "MARK-7F3A"
GATE0_CONTROL_REL = "gate0/control_reference.png"


def _char_bitmap(ch: str) -> list[str]:
    """5×7 bitmap для hex/upper (упрощённо)."""
    fonts: dict[str, list[str]] = {
        "M": ["#   #", "## ##", "# # #", "#   #", "#   #", "#   #", "#####"],
        "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
        "R": ["#### ", "#   #", "#   #", "#### ", "#  # ", "#   #", "#   #"],
        "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
        "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
        "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
        "3": [" ### ", "#   #", "    #", " ### ", "    #", "#   #", " ### "],
        "-": ["     ", "     ", "     ", " ### ", "     ", "     ", "     "],
    }
    return fonts.get(ch, ["     "] * 7)


def write_gate0_control_png(path: Path, *, width: int = 128, height: int = 96) -> None:
    """PNG с секретной надписью (пиксели), без Pillow."""
    from agents.agent_tools.image.png_codec import _encode_png_rgba

    bg = (240, 240, 245, 255)
    fg = (200, 30, 80, 255)
    pixels = [bg] * (width * height)

    def set_px(x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = color

    text = GATE0_VISUAL_SECRET
    scale = 2
    ox, oy = 8, height // 2 - 7 * scale // 2
    cx = ox
    for ch in text:
        rows = _char_bitmap(ch)
        for row_i, row in enumerate(rows):
            for col_i, cell in enumerate(row):
                if cell != " ":
                    for dy in range(scale):
                        for dx in range(scale):
                            set_px(cx + col_i * scale + dx, oy + row_i * scale + dy, fg)
        cx += 6 * scale

    # Уникальный маркер: магента-крест (не описан в metadata теста).
    for d in range(-4, 5):
        set_px(12 + d, 12, (233, 30, 99, 255))
        set_px(12, 12 + d, (233, 30, 99, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    _encode_png_rgba(path, width, height, pixels)
