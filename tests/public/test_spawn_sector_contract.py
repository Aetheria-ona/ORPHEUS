"""Shared geometric invariants for the reconstructed spawn search."""

import json
import math
import unittest
from types import SimpleNamespace

from ow174.game.collision import World
from ow174.game.npc_navigation import spawn_candidate_probes
from tests.public.test_behavior_contracts import FIXTURES


class SpawnSectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((FIXTURES / "spawn-sector-v1.json").read_text(encoding="utf-8"))
        cls.inputs = fixture["input"]
        cls.expected = fixture["expected"]
        cls.mover = SimpleNamespace(**cls.inputs["mover"])

    def probes(self, *, center=(0, 2.25, 0), direction=(0, 0, 1), angle=0, wall=None, floor_end=8):
        y = self.inputs["floor_y"]
        geometry = [
            (-8, y, -8, -8, y, floor_end, 8, y, floor_end),
            (-8, y, -8, 8, y, floor_end, 8, y, -8),
        ]
        if wall is not None:
            geometry += [
                (-8, y, wall, 8, y, wall, 8, y + 4, wall),
                (-8, y, wall, 8, y + 4, wall, -8, y + 4, wall),
                (-8, y, wall, 8, y + 4, wall, 8, y, wall),
                (-8, y, wall, -8, y + 4, wall, 8, y + 4, wall),
            ]
        request = SimpleNamespace(position=center, direction=direction, half_angle=angle,
                                  radius=self.inputs["radius"], max_candidates=self.inputs["max_candidates"])
        return list(spawn_candidate_probes(World(geometry), request, self.mover))

    def test_zero_width_sector_and_vertical_direction_fallback(self):
        cases = self.inputs["zero_width_cases"]
        for case, expected in zip(cases, self.expected["first_zero_width_points"], strict=True):
            with self.subTest(case=case):
                self.assertEqual(list(self.probes(**case)[0]), expected)

    def test_sector_rotates_with_the_axis(self):
        cases = zip(self.inputs["direct_half_angles_degrees"],
                    self.expected["first_probe_against_45_degree_cone"], strict=True)
        for degrees, classification in cases:
            with self.subTest(degrees=degrees):
                angle = math.radians(degrees)
                forward = self.probes(angle=angle)
                rotated = self.probes(angle=angle, direction=(-1, 0, 0))
                self.assertEqual([(-p.z, p.y, p.x) for p in forward], [tuple(p) for p in rotated])
                first = forward[0]
                if classification == "inside":
                    self.assertLess(abs(first.x), first.z)
                elif classification == "within_closed_cone":
                    for p in forward:
                        self.assertLessEqual(abs(p.x), p.z)
                else:
                    self.assertEqual(classification, "outside")
                    self.assertGreater(abs(first.x), first.z)

    def test_floor_support_and_wall_are_independent(self):
        flat = self.probes()
        wall_z = self.inputs["wall_z"]
        end_z = self.inputs["floor_end_z"]
        self.assertTrue(any(p.z < wall_z for p in flat))
        self.assertTrue(any(p.z > wall_z for p in flat))
        self.assertTrue(any(end_z < p.z < wall_z for p in flat))
        self.assertTrue(all(p.y == self.inputs["floor_y"] for p in flat))
        self.assertEqual(self.probes(wall=wall_z), [p if p.z < wall_z else None for p in flat])
        self.assertEqual(self.probes(floor_end=end_z), [p if p.z <= end_z else None for p in flat])
