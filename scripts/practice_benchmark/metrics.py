"""Local benchmark accounting. Times use the same monotonic clock on both processes."""

import csv
import math
from dataclasses import astuple, dataclass, fields


@dataclass
class Tick:
    frame: int
    missed: int
    wall_ns: int
    cpu_ns: int
    match_ns: int
    delivery_ns: int
    sends: int
    ingress_ns: int


def percentiles(values):
    ordered = sorted(values)
    return (
        {f"p{p}_ms": round(ordered[math.ceil(p * len(ordered) / 100) - 1] / 1e6, 3) for p in (50, 95, 99)}
        if ordered
        else {}
    )


def offered_range(offers, start, end):
    """The client logs its successful send timestamps and consecutive transport sequences."""
    selected = [seq for stamp, seq in offers if start <= stamp < end]
    return (len(selected), selected[0], selected[-1]) if selected else (0, None, None)


class Traffic:
    def __init__(self):
        self.read = self.sequence_gaps = self.stale = self.command_frame_gaps = 0
        self.accepted_sequences = []

    def received(self, before, after, previous_command, command):
        self.read += 1
        step = (after - before) & 0xFFFFFFFF
        if step == 0:
            self.stale += 1
            return
        self.sequence_gaps += step - 1
        if command != previous_command:
            self.accepted_sequences.append((after - 1) & 0xFFFFFFFF)
            if previous_command is not None:
                self.command_frame_gaps += ((command - previous_command) & 0xFFFFFFFF) - 1

    def summary(self, offers):
        count, first, _ = offers
        current = sum(((seq - first) & 0xFFFFFFFF) < count for seq in self.accepted_sequences) if count else 0
        return {
            "offered": count,
            "read": self.read,
            "accepted": len(self.accepted_sequences),
            "accepted_current": current,
            "sequence_gaps": self.sequence_gaps,
            "stale": self.stale,
            "command_frame_gaps": self.command_frame_gaps,
        }


def write_ticks(path, rows):
    with path.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(field.name for field in fields(Tick))
        writer.writerows(astuple(row) for row in rows)


def summarize(rows):
    if not rows:
        raise ValueError("no server tick started in the observation window")
    return {
        "callbacks": len(rows),
        "missed_ticks": sum(r.missed for r in rows),
        "callbacks_with_misses": sum(r.missed > 0 for r in rows),
        "tick_wall": percentiles(r.wall_ns for r in rows),
        "tick_cpu": percentiles(r.cpu_ns for r in rows),
        "match_update": percentiles(r.match_ns for r in rows),
        "send_frame_on_send_ticks": percentiles(r.delivery_ns for r in rows if r.sends),
        "tick_plus_ingress_wall": percentiles(r.wall_ns + r.ingress_ns for r in rows),
        "mean_tick_cpu_ms": round(sum(r.cpu_ns for r in rows) / (len(rows) * 1e6), 3),
    }
