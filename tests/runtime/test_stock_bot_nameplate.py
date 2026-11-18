"""Check the stock Practice Range bot health and nameplate graph on committed owner packets."""

import unittest
from collections import defaultdict

from ow174.game import collision, combat, world
from ow174.game.commands import FIRE, Command
from ow174.game.content import PRACTICE_RANGE, SOLDIER
from ow174.game.link import LinkCipher
from ow174.game.match import Match, sends
from ow174.game.script import decode, graph
from ow174.game.script.bot import PRACTICE_NAMEPLATE, PRACTICE_NAMEPLATE_PARENT
from ow174.game.server import FIRST_TICK, Client, GameServer
from tests.runtime.test_game_combat import aim
from tests.runtime.test_game_health import bar
from tests.runtime.test_game_packet_budget import KEY_IN, KEY_OUT, MemorySocket
from tests.runtime.test_game_repeated_lifecycles import wire_frame
from tests.runtime.test_server_journal import FixedSource


class StockNameplateTests(unittest.TestCase):
    def test_range_bots_send_health_and_the_authored_child_through_respawn(self):
        scene = PRACTICE_RANGE
        terrain = collision.world_for(scene.map_guid, scene.name, build_missing=False, mode=scene.mode_guid)
        if terrain is None:
            self.skipTest("requires prepared Practice Range collision")
        source = FixedSource()
        provider = collision.StaticCollisionProvider(terrain)
        match = Match(scene, environment=source, collision_provider=provider)
        self.assertTrue(match.bots, "the prepared map must contain its stock bot placements")
        prepared = [graph.entity_body(bot.body) is not None for bot in match.bots]
        # Older runtimes carry health but lack every stock bot body definition.
        has_scripts = any(prepared)
        if has_scripts:
            self.assertTrue(all(prepared), "some stock bot body definitions are missing")
        player = match.add_player(1, "Player", SOLDIER, 0, False)
        friend = next(bot for bot in match.bots if bot.team == player.team)
        # This authored stationary target is visible from the stock Soldier spawn.
        enemy = match.bots[2]
        self.assertNotEqual(enemy.team, player.team)
        server = GameServer(environment=source)
        server.sock = MemorySocket()
        client = Client(server, ("memory.test", 1), 1, LinkCipher(KEY_IN, KEY_OUT), player)
        peer = LinkCipher(KEY_OUT, KEY_IN)
        player.client, client.open = client, True
        server.clients[1] = client
        server.matches.append(match)
        match.tick = FIRST_TICK
        match.map_loaded(player, match.started)

        bodies, health, nameplates = {}, defaultdict(list), set()
        retired, replacement, hit = set(), None, False
        tracked = {friend.entity, enemy.entity}
        selected = False
        for tick in range(FIRST_TICK + 1, FIRST_TICK + 700):
            now = match.started + (tick - FIRST_TICK) * player.quanta / 1_000_000
            if (not selected and player.steps_done == 4 and client.controller_ready
                    and player.mode_script.delivered):
                if not player.has_body:
                    self.assertTrue(match.switch_hero(player, player.hero))
                selected = True
            if selected and not enemy.dead and enemy in match.bots:
                x, y, z = enemy.position
                yaw, pitch = aim(match.combat._eye(player), (x, y + 1.0, z))
                fire = FIRE if player.body_script.create_arrived else 0
                player.take_commands([Command(tick, yaw=yaw, pitch=pitch, buttons=fire)])
            match.update(now, tick)
            if enemy not in match.bots:
                fresh = next((bot for bot in match.bots if bot.spawner is enemy.spawner), None)
                if fresh is not None:
                    replacement = fresh.entity
                    tracked.add(replacement)
            if not sends(tick):
                continue
            sequence = client.seq
            client.send_frame(tick, now)
            if sequence not in client.in_flight:
                continue
            packet = peer.open(server.sock.sent.pop()[0])
            self.assertIsNotNone(packet)
            self.assertEqual(packet.seq, sequence)
            notices, records = wire_frame(packet)
            hit |= combat.HIT in notices
            for update, fields in records:
                entity = update.entity
                if entity not in tracked:
                    continue
                if update.op == world.OP_CREATE:
                    self.assertNotIn(entity, bodies)
                    bodies[entity] = decode.ClientBody()
                if update.chunk is not None:
                    self.assertTrue(client.bot_create_acked(entity))
                    model = bodies[entity]
                    model.apply(update)
                    for body_graph, parent in model.records.values():
                        if body_graph == PRACTICE_NAMEPLATE:
                            self.assertIsNotNone(parent)
                            attachment = (model.graphs[parent[0]], parent[1])
                            self.assertEqual(attachment, (PRACTICE_NAMEPLATE_PARENT, 3))
                            self.assertTrue(player.mode_script.delivered)
                            nameplates.add(entity)
                if 51 in fields:
                    pools = fields[51]
                    plain, over = bar([pools.get(i) for i in range(max(pools) + 1)])
                    current = sum(value for value, _ in plain) + sum(over)
                    health[entity].append(current)
                    if has_scripts and entity == enemy.entity and 0 < current < enemy.health:
                        self.assertIn(PRACTICE_NAMEPLATE, bodies[entity].graphs.values())
                if update.op == world.OP_DESTROY:
                    retired.add(entity)
            client.acked((sequence + 1) % 2**32, 1)
            if replacement in nameplates and health[replacement]:
                break
        self.assertTrue(hit)
        self.assertIn(enemy.entity, retired)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement, enemy.entity)
        expected = {friend.entity, enemy.entity, replacement} if has_scripts else set()
        self.assertEqual(nameplates, expected)
        self.assertEqual(health[friend.entity][0], friend.health)
        self.assertEqual(health[enemy.entity][0], enemy.health)
        self.assertTrue(any(0 < hp < enemy.health for hp in health[enemy.entity][1:-1]))
        self.assertEqual(health[enemy.entity][-1], 0.0)
        self.assertEqual(health[replacement][0], enemy.health)
