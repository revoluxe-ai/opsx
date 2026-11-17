"""Тесты форматирования времени для панели OPSX."""
from __future__ import annotations

import unittest

from opsx_cli.display_time import format_moscow_wall_clock, format_run_record_times


class DisplayTimeTests(unittest.TestCase):
    def test_utc_iso_to_moscow_without_fraction_and_offset(self) -> None:
        raw = "2026-11-17T03:29:13.826583+00:00"
        self.assertEqual(format_moscow_wall_clock(raw), "2026-11-17 06:29:13")

    def test_run_record_times_formats_known_fields(self) -> None:
        record = {
            "run_id": "abc",
            "started_at": "2026-11-17T03:29:13.826583+00:00",
            "finished_at": "2026-11-17T04:00:00+00:00",
        }
        formatted = format_run_record_times(record)
        self.assertEqual(formatted["started_at"], "2026-11-17 06:29:13")
        self.assertEqual(formatted["finished_at"], "2026-11-17 07:00:00")
        self.assertEqual(record["started_at"], "2026-11-17T03:29:13.826583+00:00")


if __name__ == "__main__":
    unittest.main()