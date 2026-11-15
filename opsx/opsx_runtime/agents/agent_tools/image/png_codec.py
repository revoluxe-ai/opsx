"""Project-independent image primitives for deterministic image tool ports."""
from __future__ import annotations
from pathlib import Path


def read_png_size(path: Path) -> tuple[int, int]:
    """Validate a PNG and return dimensions using the image provider's codec."""
    from PIL import Image
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("Expected PNG")
        return image.size


def _encode_png_rgba(path: Path, width: int, height: int, pixels) -> None:
    from PIL import Image
    image = Image.new("RGBA", (width, height))
    image.putdata(pixels)
    image.save(path, format="PNG")


def _decode_png_rgba(path: Path):
    from PIL import Image
    with Image.open(path) as source:
        image = source.convert("RGBA")
        return image.width, image.height, list(image.getdata())


def _resize_rgba(width: int, height: int, pixels, target_width: int, target_height: int):
    from PIL import Image
    image = Image.new("RGBA", (width, height))
    image.putdata(pixels)
    return list(image.resize((target_width, target_height), Image.Resampling.LANCZOS).getdata())
