import json
import math
import unittest

from ow174.game import collision, content, escort
from ow174.game.commands import Command
from ow174.game.mover import Mover, MoverData
from ow174.game.script import expr
from tests.public.test_escort_objective_placeables_contract import FIXTURES


class EscortRoundContractTests(unittest.TestCase):
    def setUp(self):
        contract = json.loads((FIXTURES / "escort-round-v1.json").read_text(encoding="utf-8"))
        self.request = contract["input"]
        self.expected = contract["expected"]
        self.assembly_ends = self.request["assembly_ends"]
        line = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (14.0, 0.0, 0.0))
        goals = self.request["checkpoints"]
        rail = (
            content.EscortRailPoint((0.0, 0.0, 0.0), line),
            content.EscortRailPoint((14.0, 0.0, 0.0), line, goals[0]),
            content.EscortRailPoint((28.0, 0.0, 0.0), line, goals[1]),
            content.EscortRailPoint((42.0, 0.0, 0.0), checkpoint=goals[2]),
        )
        self.round = escort.EscortRound(content.EscortMap(
            self.request["placement"], rail, (), tuple(self.request["stage_seconds"]),
            room_placeables=tuple(
                content.EscortRoomPlacement(
                    row["index"], tuple(tuple(room) for room in row["rooms"]), row["door"],
                    () if row.get("viewers") else (
                        ("sphere", collision.MOVER if row["door"] else
                         (collision.TEAM1, collision.TEAM0)[row["rooms"][0][1]], (0, 0, 0), 1),
                    ),
                    (row["index"], 0, 0), viewers=tuple(row.get("viewers", ())),
                    open_seconds=row.get("open_seconds", 2), close_seconds=row.get("close_seconds", 2),
                    open_delay=row.get("open_delay", 0),
                )
                for row in self.request["room_placeables"]
            ),
        ))

    def test_push_through_both_checkpoints_and_finish_once(self):
        mode = self.round
        self.assertEqual(mode.mode_state(0, self.assembly_ends).entity_vars[escort.COUNTDOWN][0],
                         self.assembly_ends)
        mode.start_setup(self.assembly_ends)
        for step, want in zip(self.request["steps"], self.expected["steps"], strict=True):
            with self.subTest(now=step["now"]):
                mode.advance(step["now"], step["players"])
                snapshot = mode.mode_state(step["now"], self.assembly_ends)
                self.assertEqual({
                    "phase": mode.phase.name,
                    "stage": mode.stage,
                    "distance": round(mode.distance, 5),
                    "seconds": snapshot.entity_vars[escort.COUNTDOWN][0],
                    "contested": mode.contested,
                    "winner": mode.winner,
                }, want)

    def test_overtime_expires_when_attackers_leave_the_cart(self):
        mode = self.round
        mode.start_setup(self.assembly_ends)
        mode.advance(mode.deadline, ())
        end = mode.deadline
        at_cart = [(team, (0, 0, 0)) for team in (escort.ATTACKERS, escort.DEFENDERS)]
        mode.advance(end, at_cart)
        self.assertTrue(mode.overtime)
        self.assertEqual(mode.mode_state(end, self.assembly_ends).entity_vars[6971][0],
                         self.expected["overtime_grace"])
        mode.advance(end + 1, ())
        mode.advance(end + self.expected["overtime_grace"] + 1, at_cart)
        self.assertEqual(mode.winner, self.expected["timeout_winner"])
        self.assertEqual(mode.phase, escort.Phase.COMPLETE)

    def test_presentation_follows_phase_and_waits_for_hero_select_receiver(self):
        mode = self.round
        expected = self.expected["presentation"]
        waiting = mode.mode_state(0, None)
        before = mode.mode_state(0, self.assembly_ends, hero_select_ready=False)
        assembly = mode.mode_state(1, self.assembly_ends, hero_select_ready=True)
        wait_state = expected["waiting_sender_state"]
        assembly_state = expected["assembly_sender_state"]
        self.assertIn(wait_state, waiting.states)
        self.assertNotIn(escort.COUNTDOWN, waiting.entity_vars)
        self.assertNotIn(assembly_state, before.states)
        self.assertNotIn(wait_state, before.states)
        self.assertIn(assembly_state, assembly.states)
        self.assertNotIn(wait_state, assembly.states)
        self.assertEqual(assembly.entity_vars[1923][0], expr.Asset(int(expected["assembly_heading"], 16)))
        self.assertEqual(assembly.entity_vars[escort.OBJECTIVE_KIND][0],
                         expr.Asset(int(expected["escort_objective"], 16)))
        mode.start_setup(self.assembly_ends)
        setup = mode.mode_state(self.assembly_ends, self.assembly_ends)
        self.assertNotIn(assembly_state, setup.states)
        self.assertIn(expected["setup_sender_state"], setup.states)
        self.assertEqual(setup.phase_states, tuple(expected["setup_receiver_states"]))
        self.assertNotIn(expected["escort_hud_state"], setup.states)
        self.assertEqual(setup.entity_vars[1923][0], expr.Asset(int(expected["setup_heading"], 16)))
        start = mode.deadline
        mode.advance(start + 5, [(escort.ATTACKERS, (0, 0, 0))])
        active = mode.mode_state(start + 5, self.assembly_ends)
        self.assertNotIn(expected["setup_sender_state"], active.states)
        self.assertIn(expected["active_sender_state"], active.states)
        self.assertEqual(active.phase_states, tuple(expected["active_receiver_states"]))
        self.assertIn(expected["escort_hud_state"], active.states)
        self.assertTrue(mode.payload_state().active)
        self.assertEqual(active.entity_vars[1923][0], expr.Asset(int(expected["defend_heading"], 16)))
        attacker = mode.mode_state(start + 5, self.assembly_ends, viewer_team=escort.ATTACKERS)
        self.assertEqual(attacker.entity_vars[1923][0], expr.Asset(int(expected["attack_heading"], 16)))
        self.assertEqual(active.entity_vars[13468][0], 0.5)
        self.assertEqual(escort.MODE_HUD_VALUES, int(expected["escort_hud_values_graph"], 16))
        self.assertEqual(escort.MODE_HUD, int(expected["escort_hud_graph"], 16))
        self.assertEqual(escort.MODE_HUD_HEADING, expected["escort_hud_heading_state"])
        self.assertEqual(escort.HUD_PROGRESS_BINDINGS, tuple(expected["escort_hud_progress_bindings"]))
        context = active.entity_vars[int(expected["escort_hud_round_data"], 16)][0]
        self.assertEqual(context.get(int(expected["escort_hud_clock"], 16)), 55)
        self.assertEqual(context.get(escort.CHECKPOINT_INDEX), 0)
        self.assertIs(setup.entity_vars[int(expected["escort_hud_live_flag"], 16)][0], False)
        self.assertIs(active.entity_vars[int(expected["escort_hud_live_flag"], 16)][0], True)
        self.assertIs(active.hud_vars[0x593E][0], False)
        self.assertIs(attacker.hud_vars[0x593E][0], True)
        self.assertEqual(active.entity_vars[59][0], escort.Phase.ACTIVE)
        self.assertEqual(active.entity_vars[2190][0], expr.Entity(mode.payload))
        self.assertEqual(active.instance_vars[int(expected["escort_hud_cart_reference"], 16)][0],
                         expr.Entity(mode.payload))

    def test_gate_presentation_plays_once_and_late_viewers_get_the_open_pose(self):
        mode = self.round
        gate = self.expected["gate_presentation"]
        entity = escort.MAP_ENTITY | gate["index"]
        other = escort.MAP_ENTITY | gate["remaining_gate"]
        mode.start_setup(self.assembly_ends)
        baseline = mode.door_state(entity, self.assembly_ends, None)
        sample = mode.door_state(entity, self.assembly_ends, baseline)
        closed = mode.door_state(entity, self.assembly_ends, sample)
        self.assertEqual([sorted(state.states) for state in (baseline, sample, closed)],
                         gate["closed_wire_states"])
        mode.advance(mode.deadline, ())
        mode.advance(gate["opens_at"], [(escort.ATTACKERS, (0, 0, 0))])
        release = mode.door_state(entity, gate["opens_at"], closed)
        self.assertEqual(release.states, closed.states)
        self.assertEqual(mode.door_state(entity, gate["clip_starts_at"] - 0.1, release), release)
        opening = mode.door_state(entity, gate["clip_starts_at"], release)
        self.assertEqual(opening.ends_at, gate["settles_at"])
        self.assertEqual(mode.door_state(entity, gate["settles_at"] - 0.1, opening), opening)
        opened = mode.door_state(entity, gate["settles_at"], opening)
        self.assertEqual([sorted(state.states) for state in (opening, opened)], gate["open_wire_states"])
        self.assertEqual([s.pose.name for s in (closed, opening, opened)], gate["states"])
        late = mode.door_state(entity, gate["settles_at"], None)
        self.assertEqual(late.pose, opened.pose)
        late_sample = mode.door_state(entity, gate["settles_at"], late)
        self.assertEqual(mode.door_state(entity, gate["settles_at"], late_sample), opened)
        self.assertEqual(mode.door_state(other, gate["settles_at"], None).pose, closed.pose)
        movement = mode.door_movement(entity)
        self.assertEqual(movement.position, tuple(gate["position"]))
        self.assertEqual(movement.velocity, (0.0, 0.0, 0.0))

    def test_rooms_release_walls_and_open_doors_in_checkpoint_order(self):
        mode = self.round
        world = mode.collision_world(collision.World([]))
        mode.start_setup(self.assembly_ends)
        for step, wanted in zip(self.request["steps"], self.expected["rooms"], strict=True):
            with self.subTest(now=step["now"]):
                mode.advance(step["now"], step["players"])
                for field, team, mask in (
                    ("visible_defense", escort.DEFENDERS, collision.TEAM0),
                    ("visible_attack", escort.ATTACKERS, collision.TEAM1),
                ):
                    self.assertEqual(mode.room_barriers(team),
                                     {escort.MAP_ENTITY | i for i in wanted[field]})
                    shapes = world.engine.query((38, -2, -2), (64, 2, 2))
                    self.assertCountEqual(
                        [shape.frame[0][0] for shape in shapes if shape.mask & mask],
                        wanted["closed"] + wanted[field],
                    )
                    effects = [70 + team] if mode.stage == 0 else []
                    self.assertEqual(mode.room_visuals(team),
                                     {escort.MAP_ENTITY | i for i in wanted[field] + effects})
                self.assertEqual(
                    {entity & ~escort.MAP_ENTITY for entity in mode.doors if not mode.door_open(entity)},
                    set(wanted["closed"]),
                )

    def test_payload_collision_follows_the_cart_without_mutating_the_map_cache(self):
        mode = self.round
        mode.payload_shapes = (collision.engine_shape(("sphere", 0x18, (0, 0, 0), 1.0)),)
        base = collision.World([])
        world = mode.collision_world(base)
        near = (-2, -2, -2), (2, 2, 2)
        self.assertEqual(base.engine.query(*near), [])
        self.assertTrue(world.engine.query(*near))
        mode.start_setup(self.assembly_ends)
        mode.advance(mode.deadline + 5, [(escort.ATTACKERS, (0, 0, 0))])
        self.assertEqual(world.engine.query(*near), [])
        self.assertTrue(world.engine.query((5, -2, -2), (9, 2, 2)))

    def test_cart_supports_a_rider_while_advancing_uphill(self):
        sample, expected = self.request["rider"], self.expected["rider"]
        end = tuple(sample["checkpoint"])
        rail = (
            content.EscortRailPoint((0, 0, 0), ((0, 0, 0), (0, 0, 0), end)),
            content.EscortRailPoint(end, checkpoint=30),
        )
        shape = ("sphere", collision.MOVER, (0, 0, 0), sample["support_radius"])
        mode = escort.EscortRound(content.EscortMap(20, rail, (), (240.0,), (shape,)))
        world = mode.collision_world(collision.World([]))
        geometry = mode.payload_collision
        support = world.ground(0, 0, 2, 0.5)
        rider = Mover((0, support.y, 0), 0, world, MoverData(), category=collision.TEAM1)
        mode.start_setup(0)
        now, dt = mode.deadline, sample["seconds"]
        mode.advance(now, ())
        start = geometry.rider(rider)
        for tick in range(1, sample["ticks"] + 1):
            rider.step(Command(tick), dt)
            anchor = geometry.rider(rider)
            self.assertIsNotNone(anchor)
            mode.advance(now + tick * dt, [(escort.ATTACKERS, rider.position)])
            geometry.carry(rider, anchor, dt)
        self.assertEqual(mode.movement().body_pitch, expected["body_pitch"])
        self.assertGreater(mode.distance, expected["min_progress_m"])
        self.assertLess(math.dist(geometry.rider(rider), start), expected["max_drift_m"])
