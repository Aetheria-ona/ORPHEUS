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
SPEED = 45.0  # m/s: the dash's top speed with the prepared 104319 runtime
TURN = 364  # yaw units per frame turned during the dash, about 2 degrees


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
        dash moved in and the frames of the owner's records."""
        start = list(player.mover.position)
        dashed, records, yaw = [], [], 0
        for step in range(hold + frames):
            self.frame += 1
            buttons = SECONDARY if step < hold else 0
            if cancel and step == hold - 1:
                buttons |= FIRE
            if self.state(punch, DASH).active:
                yaw += TURN
            snap = self.harness.command(player, self.frame, buttons=buttons, yaw=yaw)
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
        # The client cannot run the server-only dash: it gets the body's state from its first frame on.
        self.assertEqual(records[0], dashed[0] - 1)
        self.assertGreater(records[-1], dashed[-1] + 10)
        self.assertEqual(records, list(range(records[0], records[-1] + 1)))
        self.assertIsNone(player.impulse_correction)

    def test_full_charge_dashes_three_times_as_far(self):
        player, punch = self.spawn(flat_world(1.0))
        travel, dashed, _ = self.punch(player, punch, 90)
        self.assertEqual(len(dashed), 27)
        self.assertAlmostEqual(travel[2], 20.10, delta=0.01)

    def test_a_cancelled_charge_stays_in_place_without_records(self):
        player, punch = self.spawn(flat_world(1.0))
        travel, dashed, records = self.punch(player, punch, 30, cancel=True)
        self.assertEqual((travel, dashed, records), ([0.0, 0.0, 0.0], [], []))
        self.assertFalse(self.state(punch, DASH).counter)
        self.assertFalse(self.state(punch, DASH_MOD).counter)

    def test_a_wall_stops_the_dash(self):
        player, punch = self.spawn(World(FLOOR + wall(-5.0, 4.0, 5.0, 4.0, 1.0, 4.0)))
        travel, _, records = self.punch(player, punch, 14)
        radius = player.mover.data.stand_radius
        self.assertAlmostEqual(travel[2], 4.0 - radius, delta=0.02)
        self.assertTrue(records)


if __name__ == "__main__":
    unittest.main()
