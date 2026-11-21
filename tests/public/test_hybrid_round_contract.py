import json
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import assault, content, escort, hybrid, match_audio, world
from ow174.game.match import Match
from ow174.game.script import graph
from ow174.game.server_journal import _game_map
from tests.public.test_escort_objective_placeables_contract import FIXTURES


class HybridRoundContractTests(unittest.TestCase):
    def setUp(self):
        contract = json.loads((FIXTURES / "hybrid-round-v1.json").read_text(encoding="utf-8"))
        self.request, self.expected = contract["input"], contract["expected"]
        point, cart = self.request["point"], self.request["cart"]
        first, last = self.request["checkpoints"]
        objectives = [
            {"objective": 0, "kind": "point", "placeable": point["placement"], "position": point["position"],
             "casual_seconds": point["seconds"], "capture_seconds": point["capture_seconds"],
             "capture_boxes": [[0, 0, 0, 1, 0, 1, 0, 3, 2, 3]]},
            {"kind": "payload", "placeable": cart["placement"], "position": cart["position"],
             "unlock_delay": cart["unlock_delay"], "unlock_seconds": cart["unlock_seconds"]},
            *({"objective": stage, "kind": "checkpoint", "placeable": placement, "casual_seconds": time}
              for stage, (placement, _, time) in enumerate((first, last), 1)),
        ]
        curve = [[0, 0, 0], [0, 0, 0], [14, 0, 0]]
        gate = self.request["gate"]
        self.entry = {
            "name": "Synthetic", "modes": [hex(content.HYBRID_MODE)],
            "placeables": [1], "mirrored": list(range(10, 16)),
            "objectives": objectives,
            "hybrid_objective_placeables": [obj["placeable"] for obj in objectives],
            "hybrid_rail": [
                {"position": cart["position"], "curve": curve},
                {"position": [first[1], 0, 0], "curve": curve, "checkpoint": first[0]},
                {"position": [last[1], 0, 0], "checkpoint": last[0]},
            ],
            "hybrid_room_placeables": [
                {"placeable": gate, "rooms": [[1, 1]], "kind": "checkpoint_gate", "payload_unlock": True},
            ],
            "hybrid_scene_placeables": [gate],
            "spawns": [
                {"team": 0, "position": [0, 0, 10], "objectives": [[0, "defense"], [1, "defense"]]},
                {"team": 1, "position": [0, 0, -10], "objectives": [[0, "attack"]]},
                {"team": 1, "position": [20, 0, -10], "objectives": [[1, "attack"]]},
            ],
        }
        self.mode = hybrid.HybridRound(self.load().hybrid)
        self.assembly = self.request["assembly_ends"]
        self.mode.start_setup(self.assembly)

    def load(self):
        with patch.object(content, "_map_entries", return_value={1: self.entry}):
            return content.game_map(1, content.HYBRID_MODE)

    def test_map_loading_preserves_old_catalogs_and_stage_spawns(self):
        scene = self.load()
        self.assertEqual(scene.placeables, (1, 10, 11, 12, 13, 14))
        self.assertEqual(content.spawn_point(scene, 1, 0, stage=0)[0], (0, 0, -10))
        self.assertEqual(content.spawn_point(scene, 1, 0, stage=1)[0], (20, 0, -10))
        self.entry.pop("hybrid_rail")
        old = self.load()
        self.assertIsNone(old.hybrid)
        self.assertEqual(old.placeables, (1,))

    def test_wire_journals_keep_the_scene_and_accept_older_records(self):
        scene = self.load()
        record = asdict(scene)
        record.pop("payload_speed_scale")
        record.pop("capture_speed_scale")
        record["hybrid"].pop("auto_drive")
        record["spawns"][0].pop("placeable")
        self.assertEqual(_game_map(record), scene)
        record["spawns"][0]["placeable"] = 4  # A recording made by the staged-spawn build.
        self.assertEqual(_game_map(record).spawns[0].placeable, 4)
        record["spawns"][0]["placeable"] = scene.spawns[0].placeable
        for waypoint in record["hybrid"]["route"]["rail"]:
            for field in ("wait_seconds", "auto_drive_to", "open_gate", "close_gate", "gate_impact"):
                waypoint.pop(field)
        for door in record["hybrid"]["route"]["room_placeables"]:
            door.pop("gate_tag")
            door.pop("close_delay")
            door.pop("gate_debris")
            door.pop("restricted_sign")
        self.assertEqual(_game_map(record), scene)
        record["hybrid"]["auto_drive"] = True
        self.assertTrue(_game_map(record).hybrid.auto_drive)
        record["hybrid"]["auto_drive"] = False
        self.assertEqual(_game_map(record), scene)
        record.pop("hybrid")
        self.assertIsNone(_game_map(record).hybrid)

    def test_capture_unlock_checkpoint_and_finish(self):
        mode = self.mode
        gate = escort.MAP_ENTITY | self.request["gate"]
        point = self.request["point"]["position"]
        self.assertTrue(mode.capture.point_state(0).waypoint_visible)
        self.assertTrue(mode.capture.point_state(0).presentation[0])
        for (now, occupants), expected in zip(self.request["steps"], self.expected["steps"], strict=True):
            with self.subTest(now=now):
                bodies = {
                    "empty": (),
                    "contested": ((100, 1, point), (101, 0, point)),
                    "point": ((100, 1, point),),
                    "cart": ((100, 1, mode.movement().position),),
                }[occupants]
                mode.advance(now, iter(bodies))
                cart = mode.payload_state()
                self.assertEqual([mode.phase.name, mode.room_stage, mode.deadline, round(mode.distance, 4),
                                  mode.door_open(gate), cart.active, mode.winner], expected)
                state = mode.mode_state(now, self.assembly)
                self.assertEqual(state.instance_vars[0x2937][0], mode.goals[min(mode.room_stage, 2)])
                self.assertEqual(state.entity_vars[0x09C3][0], (0, mode.room_stage))
                self.assertEqual(state.instance_vars[0x2931][0],
                                 escort.PAYLOAD_OBJECTIVE if mode.room_stage else assault.POINT_OBJECTIVE)
                self.assertEqual(mode.capture.point_state(0).waypoint_visible, mode.room_stage == 0)
                self.assertEqual(cart.animation == escort.PAYLOAD_UNLOCK_ANIM, now == 158)

    def test_capture_speed_changes_capture_time(self):
        scene = self.load().hybrid
        rate = self.expected["capture_speed"]
        mode = hybrid.HybridRound(scene, capture_scale=rate)
        mode.start_setup(self.assembly)
        start = self.assembly + escort.SETUP_SECONDS
        mode.advance(start, ())
        players = ((100, 1, scene.point.position),)
        duration = scene.point.capture_seconds / rate
        mode.advance(start + duration / 2, players)
        self.assertAlmostEqual(mode.capture.progress, 0.5)
        mode.advance(start + duration, players)
        self.assertTrue(mode.capture.captured)
        self.assertTrue(mode.payload_state().active)
        self.assertEqual(mode.distance, 0)

    def test_room_visibility_and_gate_debris_follow_the_route_stage(self):
        case = self.expected["room_flow"]
        gate_tag = 77
        self.entry["hybrid_rail"][1]["open_gate"] = gate_tag
        self.entry["hybrid_room_placeables"] += [
            {"placeable": case["spawn_door"], "rooms": [[0, 1]], "kind": "door"},
            {"placeable": case["sign"], "rooms": [[2, 1]], "kind": "restricted_sign", "viewers": [0]},
            {"placeable": case["gate"], "rooms": [[2, 1]], "kind": "checkpoint_gate",
             "gate_tag": gate_tag, "gate_debris": True},
            {"placeable": case["rubble"], "rooms": [[2, 1]], "kind": "gate_debris",
             "debris_rail_node": case["rubble_rail_node"]},
        ]
        mode = hybrid.HybridRound(self.load().hybrid)
        spawn_door, sign, gate = (escort.MAP_ENTITY | case[k] for k in ("spawn_door", "sign", "gate"))
        mode.waiting_for_players = True
        self.assertTrue(mode.door_open(spawn_door))
        self.assertFalse(mode.door_open(gate))
        mode.waiting_for_players = False
        self.assertFalse(mode.door_open(spawn_door))
        self.assertNotIn(sign, mode.room_visuals(escort.DEFENDERS))

        mode.phase = escort.Phase.ACTIVE
        mode.capture.captured = 1
        mode.stage = 1
        self.assertIn(sign, mode.room_visuals(escort.DEFENDERS))
        self.assertNotIn(sign, mode.room_visuals(escort.ATTACKERS))
        closed = mode.door_state(gate, 200, None)
        self.assertTrue(mode._request_route_gate(gate_tag, True, 200, 0.0))
        ends = mode.route_gate_commands[gate].ends_at
        trigger = ends + case["debris_delay"]
        before = mode.door_state(gate, trigger - 0.1, closed)
        self.assertFalse(before.debris_played)
        after = mode.door_state(gate, trigger, before)
        self.assertTrue(after.debris_played)
        self.assertEqual(mode.door_state(gate, trigger + 1, after), after)
        self.assertEqual(mode.payload_state().checkpoint_offset, case["checkpoint_offset"])

        rubble = escort.MAP_ENTITY | case["rubble"]
        self.assertEqual(mode.door_state(rubble, trigger, None).pose, escort.DoorPose.CLOSED)
        marker = mode.route_distances[case["rubble_rail_node"]]
        mode.distance, mode.next_route_event = marker - 1.0, len(mode.route_events)
        mode._push(1.0, trigger + 2)
        falling = mode.door_state(rubble, trigger + 2, None)
        self.assertEqual(falling.pose, escort.DoorPose.OPENING)
        self.assertEqual(falling.ends_at - falling.starts_at, case["rubble_seconds"])
        self.assertEqual(mode.door_state(rubble, falling.ends_at, falling).pose, escort.DoorPose.OPEN)

    def test_loading_creates_spawn_placeables_for_every_team_and_stage(self):
        required = self.expected["room_flow"]["required_spawn_placeables"]
        for spawn, index in zip(self.entry["spawns"], required, strict=True):
            spawn["placeable"] = index
        self.entry["placeables"].extend(required)
        with patch.object(graph, "mode_scripts", return_value=None):
            match = Match(self.load())
        entities = {escort.MAP_ENTITY | index for index in required}
        for stage in (0, 1):
            match.escort.capture.captured = stage
            for team in (escort.DEFENDERS, escort.ATTACKERS, content.SPECTATOR_TEAM):
                with self.subTest(stage=stage, team=team):
                    viewer = SimpleNamespace(team=team, has_body=False, entity=99)
                    records = match.spawn_updates(viewer)
                    created = {record.entity for record in records if record.op == world.OP_CREATE}
                    self.assertTrue(entities <= created)

    def test_defenders_can_win_on_a_or_in_payload_overtime(self):
        active = self.assembly + escort.SETUP_SECONDS
        self.mode.advance(active, ())
        self.mode.advance(self.mode.deadline, ())
        self.assertEqual((self.mode.winner, self.mode.room_stage), (escort.DEFENDERS, 0))

        mode = hybrid.HybridRound(self.load().hybrid)
        mode.start_setup(self.assembly)
        mode.advance(active, ())
        mode.advance(active + 10, ((100, 1, mode.capture.points[0].position),))
        cart = mode.movement().position
        mode.advance(mode.move_at, ())
        mode.advance(mode.deadline, ((100, 1, cart), (101, 0, cart)))
        self.assertTrue(mode.overtime)
        self.assertTrue(mode.contested)
        self.assertEqual(mode.distance, 0)
        mode.advance(mode.last_attacker + escort.OVERTIME_SECONDS, ())
        self.assertEqual((mode.winner, mode.room_stage), (escort.DEFENDERS, 1))

    def test_automatic_exit_stops_at_the_first_rail_node_and_preserves_the_rollback_floor(self):
        cart = next(obj for obj in self.entry["objectives"] if obj["kind"] == "payload")
        cart["auto_drive"] = True
        rail = self.entry["hybrid_rail"]
        first_node = self.request["automatic_first_node_x"]
        start = rail[0]["position"][0]
        checkpoint = rail[1]["position"][0]
        rail[0]["curve"] = [[0, 0, 0], [0, 0, 0], [first_node - start, 0, 0]]
        rail.insert(1, {"position": [first_node, 0, 0], "curve": [[0, 0, 0], [0, 0, 0],
                                                                [checkpoint - first_node, 0, 0]]})
        mode = hybrid.HybridRound(self.load().hybrid)
        mode.start_setup(self.assembly)
        mode.advance(145, ())
        mode.advance(156, ((100, 1, mode.capture.points[0].position),))
        for now, pushing, distance, animation, direction in self.expected["automatic_steps"]:
            with self.subTest(now=now):
                nearby = ((100, 1, mode.movement().position),) if pushing else ()
                mode.advance(now, nearby)
                state = mode.payload_state()
                self.assertAlmostEqual(state.distance, distance)
                self.assertEqual((state.animation, state.direction), (animation, direction))
                self.assertEqual(bool(state.pushers), pushing)
                self.assertEqual(mode.room_stage, 1)

    def test_audio_changes_from_a_to_payload_and_then_checkpoint(self):
        phase = escort.Phase.ACTIVE
        mode = content.HYBRID_MODE
        a, payload, checkpoint = (
            match_audio.Timeline(phase, seconds, stage) for stage, seconds in enumerate((20, 40, 30))
        )
        for team in (escort.DEFENDERS, escort.ATTACKERS):
            self.assertEqual(match_audio.speech(a, payload, mode, team, single_countdown=False),
                             (match_audio.POINT_A_TAKEN[team], match_audio.PAYLOAD[team]))
            self.assertEqual(match_audio.speech(payload, checkpoint, mode, team, single_countdown=False),
                             (match_audio.CHECKPOINT,))
            self.assertEqual(match_audio.speech(None, payload, mode, team, single_countdown=False), ())
