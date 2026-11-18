"""Public checks for the benchmark's accounting, independent of any game data."""

import csv
import tempfile
import unittest
from pathlib import Path

from scripts.practice_benchmark.metrics import Tick, Traffic, offered_range, summarize, write_ticks


class PracticeBenchmarkTests(unittest.TestCase):
    def test_only_successful_offers_in_the_half_open_interval_are_matched(self):
        # Two older inputs are still in the OS queue when the observation starts.
        offers = [(99, 12), (100, 13), (150, 14), (199, 15), (200, 16)]
        traffic = Traffic()
        for sequence in (11, 12, 13):
            traffic.received(sequence, sequence + 1, sequence + 20, sequence + 21)
        report = traffic.summary(offered_range(offers, 100, 200))
        self.assertEqual(
            (report["offered"], report["read"], report["accepted"], report["accepted_current"]), (3, 3, 3, 1)
        )
        self.assertEqual(traffic.summary(offered_range(offers, 300, 400))["accepted_current"], 0)

    def test_transport_gaps_and_skipped_command_frames_are_separate(self):
        traffic = Traffic()
        traffic.received(0xFFFFFFFF, 0, 100, 101)
        traffic.received(0, 3, 101, 105)
        traffic.received(3, 3, 105, 105)
        traffic.received(3, 4, 105, 105)
        self.assertEqual(
            traffic.summary((4, 0xFFFFFFFF, 2)),
            {
                "offered": 4,
                "read": 4,
                "accepted": 2,
                "accepted_current": 2,
                "sequence_gaps": 2,
                "stale": 1,
                "command_frame_gaps": 3,
            },
        )

    def test_percentiles_and_csv_use_scheduler_skips_and_actual_work(self):
        ticks = [
            Tick(10, 0, 17_000_000, 3_000_000, 2, 1, 1, 2_000_000),
            Tick(13, 2, 1_000_000, 200_000, 1, 0, 0, 2_000_000),
        ]
        report = summarize(ticks)
        self.assertEqual(report["tick_wall"], {"p50_ms": 1.0, "p95_ms": 17.0, "p99_ms": 17.0})
        self.assertEqual(report["tick_plus_ingress_wall"]["p95_ms"], 19.0)
        self.assertEqual(
            (report["callbacks"], report["missed_ticks"], report["callbacks_with_misses"]), (2, 2, 1)
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ticks.csv"
            write_ticks(path, ticks)
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([int(row["missed"]) for row in rows], [0, 2])
            with self.assertRaises(FileExistsError):
                write_ticks(path, ticks)
        with self.assertRaises(ValueError):
            summarize([])
