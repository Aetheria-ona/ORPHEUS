"""Drive the Rialto round through the loopback game link and decode what clients receive."""

import math
import unittest

from ow174.game import collision, content, escort, statescript, world
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.match import SEND_EVERY
from ow174.game.script import decode, expr, graph
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick

RIALTO = 0x0800000000000871


class EscortRoundWireTests(unittest.TestCase):
    def test_waiting_hud_is_removed_when_escort_begins_assembly(self):
        scene = content.game_map(RIALTO, content.ESCORT_MODE)
        if scene is None or scene.escort is None:
            self.skipTest("Rialto rail is required in the local 104319 runtime")
        mode = escort.EscortRound(scene.escort)
        stream = statescript.Stream(world.GAME_MODE_ENTITY)
        client = decode.ClientBody()

        waiting = mode.mode_state(0, None)
        client.apply(stream.full_frame(waiting.frame(None)))
        self.assertEqual(client.vars[(1, 59)], escort.Phase.WAITING)
        self.assertEqual(client.vars[(0, escort.COUNTDOWN)], 0)
        self.assertIn((3, 16), client.on())
        self.assertNotIn((1, 5), client.on())
        stream.delivered = stream.last

        assembling = mode.mode_state(5, 35)
        (delta,) = stream.reliable_frames(assembling.frame(waiting), 8192, full=False)
        client.apply(delta)
        self.assertEqual(client.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertEqual(client.vars[(0, escort.COUNTDOWN)], 30)
        self.assertNotIn(3, client.graphs)
        self.assertIn((1, 5), client.on())

    def test_rialto_cart_phases_checkpoints_and_win_reach_the_client_link(self):
        scene = content.game_map(RIALTO, content.ESCORT_MODE)
        if scene is None or scene.escort is None or graph.graph(escort.PAYLOAD_GRAPH) is None:
            self.skipTest("Rialto rail and payload graph are required in the local 104319 runtime")
        cache_file = collision.cache_path(RIALTO)
        if not cache_file.is_file():
            self.skipTest("Rialto collision cache is required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        # Use the existing map geometry and the real match path with controlled tick deadlines.
        geometry = collision._read_cache(RIALTO, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        attack, late = run.server.create_match(
            scene, [(1, "Attacker", content.SOLDIER, 1, False), (2, "Late", content.SOLDIER, 0, False)]
        )
        link, player = run.connect(attack)
        match = player.match
        mode = match.escort
        mode_ui, cart_ui = decode.ClientBody(), decode.ClientBody()
        self.assertIsNotNone(mode)
        checkpoint_uis = {entity: decode.ClientBody() for _, entity in mode.checkpoints}

        def receive(peer, mode_client, cart_client):
            pos = None
            for _, record in peer.records:
                if record.entity == mode.payload:
                    if record.movement is not None:
                        pos = record.movement.position
                    receiver = cart_client
                elif record.entity == world.GAME_MODE_ENTITY:
                    receiver = mode_client
                elif record.entity in checkpoint_uis:
                    receiver = checkpoint_uis[record.entity]
                else:
                    continue
                if record.chunk is not None:
                    header = BitReader(record.chunk.getvalue())
                    first = decode.w_u32(header)
                    last = first + decode.w_var(header)
                    if first <= receiver.applied + 1 and last > receiver.applied:
                        receiver.apply(record)
            peer.records.clear()
            return pos

        run.until(lambda: player.mode_script.delivered and player.escort_payload_script.delivered
                  and not player.mode_script.data_in_flight and player.escort_mode_state is not None
                  and 5 in player.escort_mode_state.states)
        run.until(lambda: {escort.MAP_ENTITY | index for index in scene.placeables} <= {
            record.entity for _, record in link.records if record.op == world.OP_CREATE
        })
        created = {record.entity for _, record in link.records if record.op == world.OP_CREATE}
        self.assertIn(mode.payload, created)
        self.assertTrue({entity for _, entity in mode.checkpoints} <= created)
        pos = receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertIn((1, 5), mode_ui.on())
        self.assertIn((1, 95), mode_ui.on())
        self.assertEqual(mode_ui.vars[(0, 1923)], expr.Asset(0x0DE0000000006115))
        for i, checkpoint in enumerate(checkpoint_uis.values()):
            self.assertEqual(checkpoint.vars[(0, escort.OBJECTIVE_KIND)], escort.PAYLOAD_OBJECTIVE)
            self.assertEqual(checkpoint.vars[(0, 10548)], i)
            self.assertEqual(checkpoint.vars[(0, 2190)], expr.Entity(mode.payload))
        self.assertEqual(cart_ui.on(), {(1, 23)})
        self.assertLess(math.dist(mode.movement().position, pos), 0.002)
        run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: player.has_body)
        first_spawn = player.spawn
        run.now = match.assemble_ends
        run.send(link)
        run.step(5)
        receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.SETUP)
        self.assertEqual(mode_ui.vars[(0, escort.COUNTDOWN)], 45)
        self.assertEqual(mode_ui.vars[(0, 1923)], expr.Asset(0x0DE0000000006116))
        self.assertFalse(player.select_open)

        late_link, late_player = run.connect(late)
        run.until(lambda: late_player.mode_script.delivered)
        late_mode, late_cart = decode.ClientBody(), decode.ClientBody()
        receive(late_link, late_mode, late_cart)
        self.assertEqual(late_mode.vars[(1, 59)], escort.Phase.SETUP)
        self.assertTrue(late_player.select_open)

        run.now = mode.deadline
        run.send(link)
        run.send(late_link)
        run.step(5)
        receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ACTIVE)
        self.assertEqual(mode_ui.vars[(0, escort.COUNTDOWN)], 240)
        self.assertIsNone(mode_ui.vars.get((0, 1923)))
        if scene.escort.payload_collision:
            x, y, z = mode.movement().position
            player.mover.position = [x, y + 0.5, z - 3.5]
            run.until(lambda: player.body_script is not None)
            first_frame = player.body_script.frame + 1
            for frame in range(first_frame, first_frame + 40):
                player.take_commands([Command(frame, forward=127)])
                run.step()
            self.assertLess(player.mover.position[2], z - 0.2)
            self.assertGreater(mode.distance, 0.0)
            receive(link, mode_ui, cart_ui)
            self.assertGreater(mode_ui.vars[(0, 13468)], 0.0)
        windows = []
        for stage in (1, 2, 3):
            end_before = mode.deadline
            # Stand next to the cart at each sample so the server advances it along the whole rail.
            for _ in range(120):
                if mode.stage == stage:
                    break
                player.mover.position = mode.movement().position
                run.now += 0.984
                run.step()
            else:
                self.fail(f"Cart never reached stage {stage}")
            run.step(5)
            run.step(-match.tick % SEND_EVERY)
            pos = receive(link, mode_ui, cart_ui)
            self.assertLess(math.dist(mode.movement().position, pos), 0.002)
            windows.append(mode.deadline - end_before)
            if stage < 3:
                self.assertEqual(mode_ui.vars[(0, 10548)], stage)
                self.assertEqual(mode_ui.vars[(1, 10551)], expr.Entity(mode.checkpoints[stage][1]))
                self.assertEqual(cart_ui.on(), {(1, 17)})
                self.assertNotEqual(player.spawn, first_spawn)
        self.assertEqual(windows, [150.0, 90.0, 0.0])
        self.assertEqual(mode.winner, 1)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.COMPLETE)
        self.assertEqual(mode_ui.vars[(1, 7525)], escort.ATTACKERS)
        self.assertIn((1, 20), mode_ui.on())  # 0F4A sends the winning team in message 0695
        self.assertEqual(cart_ui.on(), {(1, 23)})
