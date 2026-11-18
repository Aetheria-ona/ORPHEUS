import json
import unittest

from ow174.game import collision, content, escort
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
        before = mode.mode_state(0, self.assembly_ends, hero_select_ready=False)
        assembly = mode.mode_state(1, self.assembly_ends, hero_select_ready=True)
        self.assertNotIn(5, before.states)
        self.assertIn(5, assembly.states)
        self.assertEqual(assembly.entity_vars[1923][0], expr.Asset(int(expected["assembly_heading"], 16)))
        self.assertEqual(assembly.entity_vars[escort.OBJECTIVE_KIND][0],
                         expr.Asset(int(expected["escort_objective"], 16)))
        mode.start_setup(self.assembly_ends)
        setup = mode.mode_state(self.assembly_ends, self.assembly_ends)
        self.assertNotIn(5, setup.states)
        self.assertEqual(setup.entity_vars[1923][0], expr.Asset(int(expected["setup_heading"], 16)))
        start = mode.deadline
        mode.advance(start + 5, [(escort.ATTACKERS, (0, 0, 0))])
        active = mode.mode_state(start + 5, self.assembly_ends)
        self.assertIsNone(active.entity_vars[1923][0])
        self.assertEqual(active.entity_vars[13468][0], 0.5)

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
