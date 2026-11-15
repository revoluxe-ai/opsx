"""Нормализация байт файла для сверки recipe pins (LF, CRLF, пробелы, JSON).

Используется ``CheckWorkflowReceipt`` и ``content_digest`` при проверке свежести.
Запись новых pins по контракту сценарного плана остаётся LF (``file_sha256``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Final

_UTF8_BOM: Final = b"\xef\xbb\xbf"
_WS_COLLAPSE = re.compile(r"[^\S\n]+")


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex lowercase."""
    return hashlib.sha256(data).hexdigest()


def _strip_bom(data: bytes) -> bytes:
    if data.startswith(_UTF8_BOM):
        return data[len(_UTF8_BOM) :]
    return data


def _to_lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _is_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _canonical_text_bytes(data: bytes) -> bytes:
    """Канонический текст UTF-8: LF, один trailing LF, trim line ends, collapse in-line ws."""
    text = _to_lf(_strip_bom(data)).decode("utf-8")
    stripped = text.strip()
    if not stripped:
        return b"\n"
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            parsed = json.loads(stripped)
            return (
                json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            ).encode("utf-8")
        except json.JSONDecodeError:
            pass
    lines: list[str] = []
    for line in text.splitlines():
        body = line.rstrip(" \t")
        if body:
            indent_len = len(body) - len(body.lstrip(" \t"))
            indent = body[:indent_len]
            rest = body[indent_len:]
            rest = _WS_COLLAPSE.sub(" ", rest)
            lines.append(indent + rest)
        else:
            lines.append("")
    body = "\n".join(lines).rstrip("\n") + "\n"
    return body.encode("utf-8")


def file_payload_variants(data: bytes) -> tuple[bytes, ...]:
    """Варианты байт одного файла для сопоставления с существующим pin."""
    if _is_binary(data):
        return (data,)
    canonical = _canonical_text_bytes(data)
    lf_only = _to_lf(_strip_bom(data))
    raw = data
    crlf_canonical = canonical.replace(b"\n", b"\r\n")
    crlf_lf = lf_only.replace(b"\n", b"\r\n")
    unique: list[bytes] = []
    for blob in (canonical, crlf_canonical, lf_only, raw, crlf_lf):
        if blob not in unique:
            unique.append(blob)
    return tuple(unique)


def file_pin_matches(data: bytes, pinned_hex: str) -> bool:
    """True, если pin совпадает с sha256 любого варианта байт файла."""
    if not pinned_hex or len(pinned_hex) != 64:
        return False
    pinned = pinned_hex.lower()
    return any(sha256_bytes(blob) == pinned for blob in file_payload_variants(data))


def digest_for_fold(
    root_rel_and_data: list[tuple[str, bytes]],
    *,
    variant_index: int,
    prefix: bytes,
) -> str:
    """Свертка как ``content_digest``, но байты файла — variant_index из ``file_payload_variants``."""
    digest = hashlib.sha256(prefix)
    size = 0
    for rel, data in sorted(root_rel_and_data, key=lambda x: x[0]):
        variants = file_payload_variants(data)
        if variant_index >= len(variants):
            raise ValueError("RECEIPT_VARIANT_UNAVAILABLE")
        blob = variants[variant_index]
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(blob)
        size += len(blob)
        if size > 200 * 1024 * 1024:
            raise ValueError("CHECK_INPUTS_TOO_LARGE")
        digest.update(b"\0")
    return digest.hexdigest()


def content_digest_matches_stored(
    stored_hex: str,
    root_rel_and_data: list[tuple[str, bytes]],
    *,
    prefix: bytes = b"opsx-check-inputs-v1\0",
) -> bool:
    """Сравнить сохранённый digest с пересчётом на любой согласованной форме файлов."""
    if not root_rel_and_data:
        return False
    max_variants = max(len(file_payload_variants(d)) for _r, d in root_rel_and_data)
    for variant_index in range(max_variants):
        try:
            if digest_for_fold(root_rel_and_data, variant_index=variant_index, prefix=prefix) == stored_hex:
                return True
        except ValueError:
            continue
    return False
