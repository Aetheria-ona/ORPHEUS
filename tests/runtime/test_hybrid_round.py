"""Drive a retail-data Hybrid round through the encrypted match link."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import assault, collision, content, escort, hybrid, match_audio, world
from ow174.game.bits import BitReader
from ow174.game.script import decode, expr, graph
from ow174.game.statescript import Stream
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick
from tests.runtime.test_match_audio import voice_packets

KINGS_ROW = 0x08000000000000D4
HYBRID_MAPS = (0xD4, 0x1D4, 0x2AF, 0x68D, 0x6AB, 0x6B1, 0x751, 0x75E, 0x7F4, 0xA5B)
AUTO_DRIVE_MAPS = {0xD4, 0x1D4, 0x6B1, 0x751, 0x75E, 0xA5B}


class HybridRoundWireTests(unittest.TestCase):
    def load(self, guid):
        scene = content.game_map(guid, content.HYBRID_MODE)
        if scene is None or scene.hybrid is None:
            self.skipTest("the 104319 Hybrid map catalog is required")
        return scene

    def test_map_routes_have_authored_capture_area_collision_and_stage_spawns(self):
        self.load(KINGS_ROW)
        for index in HYBRID_MAPS:
            with self.subTest(map=hex(index)):
                scene = content.game_map(0x0800000000000000 | index, content.HYBRID_MODE)
                self.assertIsNotNone(scene)
                self.assertIsNotNone(scene.hybrid)
                mode = hybrid.HybridRound(scene.hybrid)
                self.assertEqual(len(mode.goals), 3)
                self.assertTrue(mode.capture.points[0].boxes)
                self.assertIsNotNone(mode.capture.points[0].outline)
                self.assertTrue(mode.payload_shapes)
                self.assertEqual(mode.stage_seconds, (180, 120))
                self.assertEqual(mode.capture.points[0].round_seconds, 300)
                self.assertTrue(mode.checkpoints[0][0] > 0)
                self.assertLess(mode.checkpoints[0][0], mode.length)
                self.assertEqual(scene.hybrid.auto_drive, index in AUTO_DRIVE_MAPS)
                if scene.hybrid.auto_drive:
                    self.assertGreater(mode.auto_drive_end, escort.PUSH_RADIUS)
                    self.assertLess(mode.auto_drive_end, mode.checkpoints[0][0])
                    mode.start_setup(100)
                    mode.advance(145, ())
                    capture = mode.capture.points[0]
                    mode.advance(145 + capture.capture_seconds + 1, ((100, 1, capture.boxes[0][4:7]),))
                    mode.advance(mode.move_at + mode.auto_drive_end / escort.AUTO_DRIVE_SPEED + 1, ())
                    self.assertAlmostEqual(mode.distance, mode.auto_drive_end)
                    self.assertFalse(mode.pushers)
                    self.assertEqual(mode.room_stage, 1)
                    self.assertEqual(mode.movement().position, mode._point(mode.auto_drive_end))
                for stage in (0, 1, 2):
                    for team in (0, 1):
                        self.assertIsNotNone(content.spawn_point(scene, team, 0, stage=stage))

    def test_eichenwalde_uses_native_gate_debris_and_room_signs(self):
        for index in (0x68D, 0x7F4):
            scene = self.load(0x0800000000000000 | index)
            mode = hybrid.HybridRound(scene.hybrid)
            self.assertTrue(graph.graph(escort.DOOR_DEBRIS_GRAPH))
            gate, = (entity for entity, room in mode.doors.items() if room.gate_debris)
            signs = {entity for entity, room in mode.overlays.items() if room.restricted_sign}
            self.assertEqual(len(signs), 3)
            self.assertFalse({entity & 0x7FFFFFFF for entity in signs} & set(scene.placeables))
            mode.phase = escort.Phase.ACTIVE
            mode.capture.captured = mode.stage = 1
            self.assertLessEqual(signs, mode.room_visuals(escort.DEFENDERS))
            self.assertTrue(signs.isdisjoint(mode.room_visuals(escort.ATTACKERS)))

            client = decode.ClientBody()
            closed = mode.door_state(gate, 100, None)
            stream = Stream(gate)
            client.apply(stream.full_frame(mode.door_frame(gate, closed, None)))
            self.assertTrue(mode._request_route_gate(mode.doors[gate].gate_tag, True, 100, 0.0))
            cue = mode.route_gate_commands[gate].ends_at + escort.GATE_DEBRIS_DELAY
            opened = mode.door_state(gate, cue, closed)
            frame = mode.door_frame(gate, opened, closed, now=cue)
            for record in stream.reliable_frames(frame, 4096, full=False):
                client.apply(record)
            self.assertEqual(client.graphs[2], escort.DOOR_DEBRIS_GRAPH)
            self.assertIn((2, 1), client.on())

    def test_kings_row_retires_a_before_escort_and_syncs_a_late_viewer(self):
        scene = self.load(KINGS_ROW)
        if (not collision.cache_path(KINGS_ROW).is_file()
                or not all(graph.graph(index) for index in
                           (assault.POINT_GRAPH, escort.PAYLOAD_GRAPH, match_audio.CAPTURE_SOUND_GRAPH))):
            self.skipTest("King's Row collision and objective graph data are required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        geometry = collision._read_cache(KINGS_ROW, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        attack, defense = run.server.create_match(
            replace(scene, custom_start=content.START_MANUALLY),
            [(1, "Attacker", content.SOLDIER, 1, False), (2, "Defender", content.SOLDIER, 0, False)],
        )
        attack_link, attacker = run.connect(attack)
        defense_link, defender = run.connect(defense)
        match, mode = attacker.match, attacker.match.escort
        point = mode.capture.entities[0]
        outline, = mode.capture.outlines
        gates = tuple(escort.MAP_ENTITY | index for index in (787, 579))
        self.assertTrue(mode.doors[gates[0]].payload_unlock)
        self.assertTrue(all(entity in mode.room_collision for entity in gates))
        watched = world.GAME_MODE_ENTITY, point, mode.payload, *(entity for _, entity in mode.checkpoints)
        clients, seen, actions = {}, {}, {}

        def receive(link):
            models = clients.setdefault(link, {entity: decode.ClientBody() for entity in watched})
            created = seen.setdefault(link, set())
            callouts = actions.setdefault(link, [])
            ui, a, cart, *checks = [models[entity] for entity in watched]
            for _, record in link.records:
                entity = record.entity
                if record.op == world.OP_CREATE:
                    created.add(entity)
                elif record.op == world.OP_DESTROY:
                    created.discard(entity)
                if entity not in models or record.chunk is None:
                    continue
                self.assertIn(entity, created)
                model = models[entity]
                reader = BitReader(record.chunk.getvalue())
                first = decode.w_u32(reader)
                if first + decode.w_var(reader) <= model.applied:
                    continue
                if model is ui and not ui.applied:
                    self.assertTrue(a.applied)
                    self.assertTrue(all(view.applied for view in checks))
                if model is cart:
                    self.assertTrue(ui.applied)
                old_goal = ui.vars.get((1, 0x2937))
                frame = model.apply(record)
                if model is ui:
                    new_goal = ui.vars.get((1, 0x2937))
                    if old_goal == expr.Entity(point) and new_goal != old_goal:
                        self.assertTrue(a.vars[1, assault.POINT_COMPLETED])
                        self.assertIsNone(a.vars[1, assault.POINT_WAYPOINT_TYPE])
                        self.assertTrue(cart.vars[0, escort.PAYLOAD_ACTIVE])
                        self.assertFalse(cart.vars[1, escort.PAYLOAD_UNLOCK_PLAYED])
                    for _, instance, node in frame.actions:
                        if instance == 1:
                            callouts.append((instance, node))
                        if instance == match_audio.CAPTURE_SOUND_INSTANCE:
                            team = escort.DEFENDERS if link is defense_link else escort.ATTACKERS
                            effect = match_audio.CHECKPOINT_EFFECTS[team == escort.ATTACKERS]
                            self.assertEqual(ui.vars[instance, match_audio.CAPTURE_EFFECT_VAR],
                                             expr.Asset(effect))
                            callouts.append((instance, node))
            link.records.clear()
            return ui, a, cart, checks

        links = [attack_link, defense_link]
        players = [attacker, defender]

        def jump(now):
            run.now = now
            for link in links:
                run.send(link)
            run.step()

        def settled(player, phase, stage):
            state = player.escort_mode_state
            points = player.assault_point_scripts
            return (state is not None and (state.phase, state.stage) == (phase, stage)
                    and not player.mode_script.data_in_flight
                    and point in player.assault_point_states and not points[point].data_in_flight
                    and player.escort_payload_state is not None
                    and not player.escort_payload_script.data_in_flight)

        run.until(lambda: all(settled(player, escort.Phase.WAITING, 0) for player in players))
        for link in links:
            ui, a, cart, checks = receive(link)
            self.assertFalse(cart.vars[0, escort.PAYLOAD_ACTIVE])
            self.assertEqual(ui.vars[0, escort.OBJECTIVE_KIND], assault.POINT_OBJECTIVE)
            self.assertIsNone(a.vars[1, assault.POINT_WAYPOINT_TYPE])
            self.assertEqual([check.vars[0, 0x2934] for check in checks], [1, 2])
            self.assertTrue(set(watched) <= seen[link])
        self.assertTrue(match.start_custom_mode(run.now))
        for link, player in zip(links, players, strict=True):
            run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: all(player.has_body for player in players))
        jump(match.assemble_ends)
        run.until(lambda: all(settled(player, escort.Phase.SETUP, 0) for player in players))
        for link in links:
            _, a, _, _ = receive(link)
            self.assertTrue(a.vars[1, assault.POINT_LOCKED])
            self.assertEqual(a.vars[1, assault.POINT_WAYPOINT_TYPE], assault.ASSAULT_WAYPOINT)

        jump(mode.deadline)
        run.until(lambda: all(settled(player, escort.Phase.ACTIVE, 0) and player.assault_hud_bound
                              and outline in player.assault_outline_states
                              for player in players))
        point_center = scene.hybrid.point.boxes[0][4:7]
        attacker.mover.position = defender.mover.position = point_center
        jump(run.now + 2)
        self.assertTrue(mode.capture.contested)
        self.assertEqual(mode.capture.progress, 0)
        defender.mover.position = defender.spawn[0]
        first_spawn, opening_deadline = attacker.spawn, mode.deadline
        jump(run.now + scene.hybrid.point.capture_seconds / 2)
        self.assertGreaterEqual(mode.capture.progress, 0.5)
        self.assertEqual(mode.distance, 0)

        def a_retired():
            return attacker.assault_point_states[point].presentation == (True, True)

        send = run.send

        def pause_point_ack(link):
            if link is not attack_link or not a_retired():
                send(link)

        with patch.object(run, "send", side_effect=pause_point_ack):
            jump(run.now + scene.hybrid.point.capture_seconds / 2)
            run.until(a_retired)
            run.step(5)
        self.assertTrue(attacker.assault_point_scripts[point].data_in_flight)
        ui, a, cart, _ = receive(attack_link)
        self.assertEqual(ui.vars[1, 0x2937], expr.Entity(point))
        self.assertIsNone(a.vars[1, assault.POINT_WAYPOINT_TYPE])
        self.assertNotIn(outline, seen[attack_link])
        self.assertFalse(cart.vars[0, escort.PAYLOAD_ACTIVE])
        self.assertEqual(mode.deadline, opening_deadline + mode.stage_seconds[0])
        self.assertNotEqual(attacker.spawn, first_spawn)
        self.assertFalse(mode.door_open(gates[0]))

        run.until(lambda: all(settled(player, escort.Phase.ACTIVE, 1) and player.escort_hud_bound
                              for player in players))
        capture_action = match_audio.CAPTURE_SOUND_INSTANCE, match_audio.CAPTURE_SOUND_ACTION
        for player, link in zip(players, links, strict=True):
            ui, a, cart, _ = receive(link)
            self.assertTrue(cart.vars[0, escort.PAYLOAD_ACTIVE])
            self.assertFalse(cart.vars[1, escort.PAYLOAD_UNLOCK_PLAYED])
            self.assertEqual(ui.vars[1, escort.HUD_CART_REFERENCE], expr.Entity(mode.payload))
            self.assertEqual(ui.vars[1, 0x2934], 1)
            self.assertEqual(cart.vars[1, escort.PAYLOAD_CHECKPOINT_OFFSET], 1)
            self.assertEqual(ui.vars[0, 0x09C3], (0, 1))
            self.assertEqual(actions[link].count(capture_action), 1)
            self.assertIn((1, escort.HUD_CART_CHANGED), actions[link])
            lines = [v["+0x90"] & 0xFFFF for v in voice_packets(link)]
            self.assertIn(match_audio.POINT_A_TAKEN[player.team][1], lines)

        jump(mode.unlock_at)
        run.until(lambda: all(player.escort_payload_state.animation == escort.PAYLOAD_UNLOCK_ANIM
                              and not player.escort_payload_script.data_in_flight for player in players))
        self.assertTrue(mode.door_open(gates[0]))
        self.assertFalse(mode.door_open(gates[1]))
        self.assertTrue(set(mode.room_collision[gates[0]]).isdisjoint(mode.payload_collision.room_shapes))
        for link in links:
            _, _, cart, _ = receive(link)
            self.assertIn((1, escort.PAYLOAD_UNLOCK_ANIM), cart.on())
            self.assertTrue(cart.vars[0, escort.PAYLOAD_ACTIVE])
            self.assertTrue(cart.vars[1, escort.PAYLOAD_UNLOCK_PLAYED])

        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (late,) = run.server.join_match(match, [(3, "Late", content.SOLDIER, 1, False)])
        late_link, late_player = run.connect(late)
        run.until(lambda: settled(late_player, escort.Phase.ACTIVE, 1) and late_player.escort_hud_bound)
        late_ui, late_a, late_cart, _ = receive(late_link)
        self.assertEqual(late_ui.vars[1, 0x2937], mode.goals[1])
        self.assertEqual(late_player.spawn, content.spawn_point(scene, 1, 1, stage=1))
        self.assertIsNone(late_a.vars[1, assault.POINT_WAYPOINT_TYPE])
        self.assertNotIn(outline, seen[late_link])
        self.assertIn((1, escort.PAYLOAD_UNLOCK_ANIM), late_cart.on())
        self.assertNotIn(capture_action, actions[late_link])
        self.assertEqual(voice_packets(late_link), [])

        links.append(late_link)
        players.append(late_player)
        attacker.mover.position = defender.mover.position = (10_000, 0, 10_000)
        jump(mode.move_at + 0.5)
        run.until(lambda: all(
            settled(player, escort.Phase.ACTIVE, 1)
            and player.escort_payload_state.animation == escort.PAYLOAD_AUTO_DRIVE_ANIM
            for player in players
        ))
        self.assertGreater(mode.distance, 0)
        self.assertFalse(mode.pushers)
        for link in links:
            _, _, cart, _ = receive(link)
            self.assertIn((1, escort.PAYLOAD_WAYPOINT), cart.on())
            self.assertNotIn((1, escort.PAYLOAD_UNLOCK_ANIM), cart.on())
            self.assertIs(cart.vars[1, escort.PAYLOAD_ENABLED], False)
            self.assertIs(cart.vars[1, escort.PAYLOAD_UNLOCK_PLAYED], True)

        jump(mode.move_at + mode.auto_drive_end / escort.AUTO_DRIVE_SPEED + 0.3)
        run.until(lambda: all(settled(player, escort.Phase.ACTIVE, 1)
                              and player.escort_payload_state.animation is None for player in players))
        self.assertAlmostEqual(mode.distance, mode.auto_drive_end)
        launch_pose = mode.movement().position
        for link in links:
            _, _, cart, _ = receive(link)
            self.assertEqual(cart.on(), {(1, escort.PAYLOAD_WAYPOINT)})
            self.assertEqual(cart.vars[0, escort.PAYLOAD_DISTANCE], expr.f32(mode.auto_drive_end))
        attacker.mover.position = launch_pose
        jump(run.now + 1)
        run.until(lambda: all(player.escort_payload_state.animation == escort.PAYLOAD_FORWARD_ANIM
                              for player in players))
        pushed_position = mode.movement().position
        self.assertNotEqual(pushed_position, launch_pose)
        attacker.mover.position = defender.mover.position
        jump(run.now + 0.3)
        run.until(lambda: all(settled(player, escort.Phase.ACTIVE, 1)
                              and player.escort_payload_state.animation is None for player in players))
        self.assertEqual(mode.movement().position, pushed_position)
        for link in links:
            _, _, cart, _ = receive(link)
            self.assertEqual(cart.on(), {(1, escort.PAYLOAD_WAYPOINT)})
            self.assertEqual(cart.vars[0, escort.PAYLOAD_DIRECTION], 0)

        crossed = False
        for _ in range(220):
            if mode.phase == escort.Phase.COMPLETE:
                break
            attacker.mover.position = mode.movement().position
            jump(run.now + 0.8)
            if mode.stage == 1 and not crossed:
                crossed = True
                self.assertEqual(mode.deadline, opening_deadline + sum(mode.stage_seconds))
                run.until(lambda: all(settled(player, escort.Phase.ACTIVE, 2) for player in players))
                for link in links:
                    ui, _, cart, _ = receive(link)
                    self.assertEqual(ui.vars[1, 0x2937], mode.goals[2])
                    offset = cart.vars[1, escort.PAYLOAD_CHECKPOINT_OFFSET]
                    self.assertEqual(ui.vars[0, escort.CHECKPOINT_INDEX] - offset, 1)
                    self.assertTrue(cart.vars[0, escort.PAYLOAD_ACTIVE])
                    self.assertIn((1, escort.PAYLOAD_WAYPOINT), cart.on())
                    self.assertEqual(cart.vars[0, escort.PAYLOAD_DIRECTION], 0)
                    self.assertEqual(cart.vars[1, escort.PAYLOAD_PUSHERS], ())
        self.assertTrue(crossed)
        self.assertEqual(mode.winner, escort.ATTACKERS)
        self.assertTrue(mode.door_open(gates[1]))
        run.until(lambda: all(settled(player, escort.Phase.COMPLETE, 2) for player in players))
        for link in links:
            ui, a, cart, _ = receive(link)
            self.assertEqual(ui.vars[0, 0x1D65], escort.ATTACKERS)
            self.assertEqual(ui.vars[0, 0x09C3], (0, 3))
            self.assertEqual(cart.vars[0, escort.PAYLOAD_DISTANCE], expr.f32(mode.length))
            self.assertFalse(cart.vars[0, escort.PAYLOAD_ACTIVE])
            self.assertTrue(cart.vars[1, escort.PAYLOAD_ENABLED])
            self.assertNotIn((1, escort.PAYLOAD_WAYPOINT), cart.on())
            self.assertTrue(a.vars[1, assault.POINT_COMPLETED])
        for player, link in zip(players, links, strict=True):
            lines = [v["+0x90"] & 0xFFFF for v in voice_packets(link)]
            if player is not late_player:
                self.assertIn(match_audio.PAYLOAD[player.team][1], lines)
            self.assertEqual(actions[link].count(capture_action), 1 if player is late_player else 2)
            self.assertIn(match_audio.CHECKPOINT[1], lines)
