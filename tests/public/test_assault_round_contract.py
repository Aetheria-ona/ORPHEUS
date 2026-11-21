import json
import math
import unittest
from dataclasses import asdict, replace
from unittest.mock import patch

from ow174.game import assault, content, escort
from ow174.game.bits import BitReader, BitWriter
from ow174.game.script import decode, expr
from ow174.game.server_journal import _game_map
from ow174.game.statescript import write_value
from tests.public.test_escort_objective_placeables_contract import FIXTURES
from tools import extract_game_maps


class AssaultRoundContractTests(unittest.TestCase):
    def setUp(self):
        contract = json.loads((FIXTURES / "assault-round-v1.json").read_text(encoding="utf-8"))
        self.request = contract["input"]
        self.expected = contract["expected"]
        self.presentation = contract["presentation"]
        self.points = tuple(content.AssaultPoint(
            row["placeable"], tuple(row["position"]), (tuple(row["box"]),),
            row["round_seconds"], row["capture_seconds"],
        ) for row in self.request["points"])
        self.mode = assault.AssaultRound(content.AssaultMap(self.points, (), ()))
        self.assembly_ends = self.request["assembly_ends"]
        self.mode.start_setup(self.assembly_ends)

    def advance(self, now, occupants):
        self.mode.advance(now, (self.request["occupants"][name] for name in occupants))

    def test_journal_reconstructs_assault_map_and_points(self):
        scene = replace(content.PRACTICE_RANGE, assault=content.AssaultMap(self.points, (), ()))
        self.assertEqual(_game_map(asdict(scene)), scene)

    def test_capture_contest_decay_unlock_and_win(self):
        mode = self.mode
        self.assertTrue(assault.contains(self.points[1], (20, 1, 3)))
        self.assertFalse(assault.contains(self.points[1], (23, 1, 0)))
        variable = int(self.presentation["waypoint_variable"], 16)
        waypoint = expr.Asset(int(self.presentation["waypoint_type"], 16))
        for (now, occupants), expected, waypoint_stage, outline_stage in zip(
            self.request["capture_steps"], self.expected["capture_steps"],
            self.expected["waypoint_stages"], self.expected["outline_stages"], strict=True,
        ):
            with self.subTest(now=now):
                self.advance(now, occupants)
                self.assertEqual([mode.phase.name, mode.stage, round(mode.progress, 6),
                                  mode.contested, mode.deadline, mode.winner], expected)
                a, b = (mode.point_state(i) for i in range(2))
                self.assertEqual(a.instance_vars[assault.POINT_COMPLETED][0], mode.captured > 0)
                self.assertEqual(
                    [point.instance_vars[assault.POINT_LOCKED][0] for point in (a, b)],
                    [mode.phase != assault.Phase.ACTIVE or index != mode.stage for index in range(2)],
                )
                self.assertIs(a.entity_vars[0x2DA4][0], True)
                self.assertEqual(a.entity_vars[escort.OBJECTIVE_KIND][0], assault.POINT_OBJECTIVE)
                self.assertEqual(
                    [point.instance_vars[variable][0] for point in (a, b)],
                    [waypoint if stage == waypoint_stage else None for stage in range(2)],
                )
                self.assertEqual([mode.outline_visible(i) for i in range(2)],
                                 [index == outline_stage for index in range(2)])
                snapshot = mode.mode_state(now, self.assembly_ends, hero_select_ready=True, viewer_team=1)
                self.assertEqual(snapshot.entity_vars[0x2937][0], expr.Entity(mode.entities[mode.stage]))
        self.advance(200, ["defender_a"])
        self.assertEqual(mode.winner, assault.ATTACKERS)

    def test_capture_milestones_feed_native_layout_and_survive_decay(self):
        for (now, occupants), expected in zip(
            self.request["milestone_steps"], self.expected["milestone_steps"], strict=True,
        ):
            with self.subTest(now=now):
                self.advance(now, occupants)
                point = self.mode.point_state(self.mode.stage)
                progress = point.entity_vars[0x268A][0]
                peak = point.entity_vars[0x26AC][0]
                sections = point.instance_vars[0x2F61][0]
                self.assertIs(type(sections), int)
                self.assertEqual(sections, self.presentation["capture_segments"])
                self.assertEqual(point.instance_vars[0x3060][0], expr.f32(1 / sections))
                self.assertEqual(point.instance_vars[0x3067][0], 0.0)
                # 0E8E's 3050.01C presenter input drives CapturedSegments in 0315.05E.
                captured = min(math.floor(expr.f32(progress * sections)), sections - 1)
                self.assertEqual([self.mode.stage, round(progress, 6), round(peak, 6), captured], expected)

    def test_waypoint_enum_uses_the_native_asset_mask(self):
        variable = int(self.presentation["waypoint_variable"], 16)
        waypoint = self.mode.point_state(0).instance_vars[variable][0]
        stream = BitWriter()
        write_value(stream, waypoint)
        reader = BitReader(stream.getvalue())
        self.assertEqual(reader.bits(4), 9)
        self.assertFalse(reader.bit())
        self.assertEqual([reader.bits(8) for _ in range(4)], self.presentation["waypoint_mask_bytes"])
        self.assertEqual(reader.pos, stream.count)
        self.assertEqual(decode.read_value(BitReader(stream.getvalue())), waypoint)

    def test_overtime_expiry_and_defender_win_without_overtime(self):
        self.advance(75, [])
        for (now, occupants), expected in zip(
            self.request["overtime_steps"], self.expected["overtime_steps"], strict=True,
        ):
            self.advance(now, occupants)
            self.assertEqual([self.mode.overtime, self.mode.winner], expected)
        self.setUp()
        self.advance(135, ["spectator_a", "attacker_above_a"])
        self.assertEqual(self.mode.winner, assault.DEFENDERS)
        self.assertFalse(self.mode.overtime)

    def test_old_catalog_and_mode_filter(self):
        points = self.request["points"]
        guid = 0x0800000000000501
        old = {
            "name": "Synthetic",
            "modes": [hex(content.ASSAULT_MODE), hex(content.ESCORT_MODE)],
            "placeables": [1],
            "mirrored": [42, 41, 43, 44],
            "health_packs": [{"placeable": 43}, {"placeable": 999}],
            "assault_scene_placeables": [44, 1000],
            "objectives": [
                {"kind": "point", "placeable": point["placeable"], "objective": i,
                 "position": point["position"]} for i, point in enumerate(points)
            ],
            "spawns": [{"team": 1, "position": [5, 0, 0], "objectives": [[0, "attack"]]},
                       {"team": 1, "position": [6, 0, 0], "objectives": [[1, "attack"]]}],
        }
        with patch.object(content, "_map_entries", return_value={guid: old}):
            scene = content.game_map(guid, content.ASSAULT_MODE)
            self.assertEqual(scene.placeables, (1, 41, 42, 43, 44))
            self.assertEqual([point.round_seconds for point in scene.assault.points], [240, 180])
            self.assertEqual(content.spawn_point(scene, 1, 0, stage=1)[0], (6, 0, 0))
            self.assertEqual(content.game_map(guid, content.ESCORT_MODE).placeables, (1, 43))

    def test_waiting_and_team_headings(self):
        self.mode.phase = assault.Phase.ASSEMBLE
        waiting = self.mode.mode_state(0, None, hero_select_ready=False, viewer_team=1)
        self.assertNotIn(escort.COUNTDOWN, waiting.entity_vars)
        self.assertFalse(any(
            self.mode.point_state(index).waypoint_visible for index in range(len(self.points))
        ))
        self.advance(0, [])
        self.mode.start_setup(self.assembly_ends)
        attacker = self.mode.mode_state(30, self.assembly_ends, hero_select_ready=True, viewer_team=1)
        defender = self.mode.mode_state(30, self.assembly_ends, hero_select_ready=True, viewer_team=0)
        self.assertEqual(attacker.entity_vars[escort.COUNTDOWN], (45, ()))
        self.assertEqual(attacker.entity_vars[0x0783][0], expr.Asset(0x0DE00000000010C4))
        self.assertEqual(defender.entity_vars[0x0783][0], expr.Asset(0x0DE00000000010C5))

    def test_outline_and_room_overlays_follow_authored_placements(self):
        data = self.presentation
        base = 41
        goal = {"objective": 0, "kind": "point", "position": data["positions"][0],
                "defense_tag": "0x310", "attack_tag": "0x341"}
        placements = [{"position": pos, "definition": 1} for pos in data["positions"]]
        graph = {extract_game_maps.ROOM_VISIBILITY_GRAPH: {0x2A12: True}}
        candidates = [(43, [0x310], graph), (44, [0xA16], graph)]
        rooms, hidden, _ = extract_game_maps.objective_rooms(
            base, placements, candidates, [goal], hide_unassigned_overlays=True,
        )
        self.assertEqual(sorted(hidden), data["hidden_overlays"])
        self.assertEqual([room["placeable"] for room in rooms], data["matched_overlays"])
        extract_game_maps.assault_outlines(base, placements, [goal], data["outline_candidates"])
        self.assertEqual(goal["outline"], data["outline"])
