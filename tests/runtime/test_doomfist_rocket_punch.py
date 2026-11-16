"""Run Rocket Punch (0D06) through real command input: its server-only dash and the owner's records."""

import math
import unittest
from unittest.mock import patch

from ow174.game import bodytrace, bots, correction, movelog
from ow174.game.collision import World, flat_world
from ow174.game.commands import FIRE, SECONDARY
from ow174.game.world import OP_CREATE
from tests.runtime import test_game_move_to as movement
from tests.runtime.test_game_mover import FLOOR, wall

PUNCH = 0x0D06
DASH, DASH_MOD = 10, 76  # node positions: the dash's Wait and its server-only MovementMod
CHARGE = 23  # the charge's ChaseVar: the client predicts the dash's registration #138 at its release
SPEED = 45.0  # m/s: the dash's top speed with the prepared 104319 runtime
TURN = 364  # yaw units per frame turned during the dash, about 2 degrees
SLOW, CHARGE_SLOW = 23184, 77  # the owner's movement slow (entity var) and the charge's Stack on it (state)


class RocketPunchTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bodytrace, "ENABLED", False))
        self.enterContext(patch.object(movelog, "ENABLED", False))
        self.harness = movement.FixedTargetMoveToTests()

    def spawn(self, geometry):
        with patch.object(bots, "make", return_value=[]):
            player, punch = self.harness.spawn(movement.DOOMFIST, PUNCH, geometry)
        [create] = [update for update in player.client.entities
                    if update.entity == player.body and update.op == OP_CREATE]
        create.stream.arrived(create.chunk_last)
        self.frame = movement.START + 1
        self.harness.command(player, self.frame)
        return player, punch

    def state(self, punch, position):
        return punch.state(punch.graph.node(position).state)

    def punch(self, player, punch, hold, *, cancel=False, frames=60):
        """Hold the punch for `hold` frames, then run on while turning during the dash. Returns the frames the
        dash moved in and the frames of the owner's records; `self.charged` the frames the charge ran in."""
        start = list(player.mover.position)
        dashed, records, yaw = [], [], 0
        self.charged = []
        for step in range(hold + frames):
            self.frame += 1
            buttons = SECONDARY if step < hold else 0
            if cancel and step == hold - 1:
                buttons |= FIRE
            if self.state(punch, DASH).active:
                yaw += TURN
            snap = self.harness.command(player, self.frame, buttons=buttons, yaw=yaw)
            if self.state(punch, CHARGE).active:
                self.charged.append(self.frame)
            if math.isclose(math.hypot(snap.velocity[0], snap.velocity[2]), SPEED, abs_tol=0.01):
                dashed.append(snap.frame)
            update = correction.own_record(player, self.frame)
            if update is not None:
                correction.sent(player, update)
                records.append(update.movement.frame)
        travel = [end - begin for begin, end in zip(start, player.mover.position, strict=True)]
        self.assertIsNone(punch.component.fault)
        return travel, dashed, records

    def test_minimum_charge_dashes_along_the_release_facing_and_the_owner_follows_it(self):
        player, punch = self.spawn(flat_world(1.0))
        travel, dashed, records = self.punch(player, punch, 14)
        self.assertEqual(len(dashed), 8)
        self.assertAlmostEqual(travel[2], 6.43, delta=0.01)
        self.assertAlmostEqual(travel[0], 0.0, places=3)  # turning does not steer the dash
        self.assertEqual(travel[1], 0.0)
        # The client cannot run the server-only dash, and it draws the view from its body's kept states
        # while it has the dash's registration on, predicted from the release: it gets the body's state
        # from the charge's first frame on, without a gap, through the dash.
        self.assertEqual(records[0], self.charged[0] + 1)
        self.assertLess(records[0], dashed[0] - 10)
        self.assertGreater(records[-1], dashed[-1] + 10)
        self.assertEqual(records, list(range(records[0], records[-1] + 1)))
        self.assertIsNone(player.impulse_correction)

    def test_full_charge_dashes_three_times_as_far(self):
        player, punch = self.spawn(flat_world(1.0))
        travel, dashed, _ = self.punch(player, punch, 90)
        self.assertEqual(len(dashed), 27)
        self.assertAlmostEqual(travel[2], 20.10, delta=0.01)

    def test_a_cancelled_charge_stays_in_place_with_records_only_while_charging(self):
        player, punch = self.spawn(flat_world(1.0))
        travel, dashed, records = self.punch(player, punch, 30, cancel=True)
        self.assertEqual((travel, dashed), ([0.0, 0.0, 0.0], []))
        self.assertEqual(records[0], self.charged[0] + 1)
        self.assertLessEqual(records[-1], self.charged[-1] + 1 + 250_000 // player.quanta)
        self.assertIsNone(player.impulse_correction)
        self.assertFalse(self.state(punch, DASH).counter)
        self.assertFalse(self.state(punch, DASH_MOD).counter)

    def test_the_charge_slow_reaches_the_client_once(self):
        """State 77 is `#23184 + 0.5`, the owner's slow. The client adds it over the value it gets, so it
        gets the value below the entry: the resolved 0.5 became 1.0 there, a full stop."""
        player, punch = self.spawn(flat_world(1.0))
        start = list(player.mover.position)
        for _ in range(30):
            self.frame += 1
            self.harness.command(player, self.frame, buttons=SECONDARY, forward=127)
        slow = player.body_script.component.entity_var(SLOW)
        self.assertAlmostEqual(slow.value(), 0.5, places=6)
        value, bindings = player.body_script.snapshot().vars[(0, SLOW)]
        self.assertIsNone(value)
        self.assertEqual([(item.instance, item.state) for item in bindings], [(punch.id, CHARGE_SLOW)])
        self.assertGreater(math.dist(start, player.mover.position), 0.5)  # the server walks it, slowed

    def test_the_owner_sees_the_dash_registration_with_its_records(self):
        """#138 (STU_4773DA56) and #141 go to the owner as on, as on retail. While #138 is on the original
        client draws the view from its body's kept states, so the owner gets records from the charge on."""
        player, punch = self.spawn(flat_world(1.0))
        shown = [self.state(punch, position) for position in (138, 141)]
        seen = []
        for step in range(14 + 20):
            self.frame += 1
            self.harness.command(player, self.frame, buttons=SECONDARY if step < 14 else 0)
            if all(state.active for state in shown):
                snap = player.body_script.snapshot()
                seen.append({(punch.id, state.index) for state in shown} <= set(snap.states))
                self.assertIsNotNone(correction.own_record(player, self.frame))
        self.assertTrue(seen)
        self.assertTrue(all(seen))

    def test_a_wall_stops_the_dash(self):
        player, punch = self.spawn(World(FLOOR + wall(-5.0, 4.0, 5.0, 4.0, 1.0, 4.0)))
        travel, _, records = self.punch(player, punch, 14)
        radius = player.mover.data.stand_radius
        self.assertAlmostEqual(travel[2], 4.0 - radius, delta=0.02)
        self.assertTrue(records)


if __name__ == "__main__":
    unittest.main()
