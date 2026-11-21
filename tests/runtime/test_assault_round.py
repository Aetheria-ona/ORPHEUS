"""Decode Assault's objective and phase streams on the encrypted game link."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import assault, collision, combat, content, escort, match_audio, world
from ow174.game.bits import BitReader
from ow174.game.script import decode, expr, graph
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick

ANUBIS = 0x080000000000005B
PARIS = 0x0800000000000891


class AssaultRoundWireTests(unittest.TestCase):
    def test_anubis_waiting_through_b_with_two_teams_and_a_late_viewer(self):
        self._check_round(ANUBIS, (767, 747), range(1060, 1064))

    def test_paris_waiting_through_b_with_two_teams_and_a_late_viewer(self):
        self._check_round(PARIS, (1510, 529))

    def _check_round(self, map_guid, outlines, suppressed_placements=()):
        scene = content.game_map(map_guid, content.ASSAULT_MODE)
        if (scene is None or scene.assault is None or not collision.cache_path(map_guid).is_file()
                or not all(graph.graph(index) for index in
                           (assault.POINT_GRAPH, assault.OUTLINE_GRAPH, match_audio.CAPTURE_SOUND_GRAPH))):
            self.skipTest("the 104319 map, collision and capture graphs are required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        geometry = collision._read_cache(map_guid, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        attack, defend = run.server.create_match(
            replace(scene, custom_start=content.START_MANUALLY),
            [(1, "Attacker", content.SOLDIER, 1, False), (2, "Defender", content.SOLDIER, 0, False)],
        )
        attack_link, attacker = run.connect(attack)
        defend_link, defender = run.connect(defend)
        match, mode = attacker.match, attacker.match.assault
        clients = {}
        shown = {}
        self.assertEqual(
            mode.outlines, {escort.MAP_ENTITY | placement: index for index, placement in enumerate(outlines)},
        )
        outline_a, outline_b = (escort.MAP_ENTITY | placement for placement in outlines)
        watched = {world.GAME_MODE_ENTITY, *mode.entities, *mode.outlines}
        suppressed = {escort.MAP_ENTITY | index for index in suppressed_placements}
        markers = {}
        captures = {}
        waypoint_type = expr.Asset(0x0030603F)

        def receive(link):
            views = clients.setdefault(link, {entity: decode.ClientBody() for entity in watched})
            active = shown.setdefault(link, set())
            changes = markers.setdefault(link, [])
            for _, record in link.records:
                entity = record.entity
                if record.op == world.OP_CREATE:
                    self.assertNotIn(entity, suppressed)
                    if entity == outline_b:
                        self.assertIs(views[mode.entities[1]].vars[1, assault.POINT_LOCKED], False)
                        self.assertNotIn(outline_a, active)
                    active.add(entity)
                elif record.op == world.OP_DESTROY:
                    active.discard(entity)
                if entity not in views or record.chunk is None:
                    continue
                view = views[entity]
                header = BitReader(record.chunk.getvalue())
                first = decode.w_u32(header)
                last = first + decode.w_var(header)
                if last <= view.applied:
                    continue
                self.assertIn(entity, active)
                if entity == world.GAME_MODE_ENTITY and not view.applied:
                    self.assertTrue(all(views[point].applied for point in mode.entities))
                old_goal = view.vars.get((0, 0x2937)) if entity == world.GAME_MODE_ENTITY else None
                previous_waypoint = view.vars.get((1, 0x3F75))
                frame = view.apply(record)
                if entity == world.GAME_MODE_ENTITY:
                    sound = (match_audio.CAPTURE_SOUND_INSTANCE, match_audio.CAPTURE_SOUND_ACTION)
                    for _, instance, node in frame.actions:
                        if (instance, node) == sound:
                            captures.setdefault(link, []).append(view.vars[0, 0x2937])
                if old_goal is not None and view.vars[0, 0x2937] != old_goal:
                    self.assertTrue(views[mode.entities[0]].vars[1, assault.POINT_COMPLETED])
                    self.assertFalse(views[mode.entities[1]].vars[1, assault.POINT_LOCKED])
                    self.assertEqual(views[mode.entities[1]].vars[1, 0x3F75], waypoint_type)
                if entity in mode.entities and view.graphs:
                    ui = views[world.GAME_MODE_ENTITY]
                    self.assertEqual(ui.graphs[1], escort.MODE_GRAPH)
                    self.assertNotIn(combat.MODE_UI, ui.graphs.values())
                    self.assertIn((1, assault.POINT_PRESENTER), view.on())
                    waypoint = view.vars[1, 0x3F75]
                    if not view.vars[1, assault.POINT_LOCKED]:
                        self.assertEqual(waypoint, waypoint_type)
                    self.assertEqual((1, assault.POINT_LOCAL_UI) in view.on(), waypoint is not None)
                    instance = frame.instances.get(1)
                    if not frame.full:
                        self.assertEqual(instance is not None and assault.POINT_LOCAL_UI in instance.states,
                                         waypoint != previous_waypoint)
                    if waypoint != previous_waypoint:
                        # The selector receives its value in the same frame as the scope edge.
                        self.assertIn(0x3F75, frame.instances[1].vars)
                    self.assertLessEqual(sum(views[point].vars.get((1, 0x3F75)) == waypoint_type
                                             for point in mode.entities), 1)
                    if waypoint != previous_waypoint:
                        changes.append((entity, waypoint is not None))
                if entity in mode.outlines:
                    point = mode.entities[mode.outlines[entity]]
                    self.assertTrue(views[point].applied)
                    self.assertIn((1, assault.OUTLINE_APPEARANCE), view.on())
                    self.assertEqual(view.vars[1, 0x2EC4], assault.DEFENDERS)
                    self.assertFalse(frame.actions)
            link.records.clear()
            return tuple(views[entity] for entity in (world.GAME_MODE_ENTITY, *mode.entities))

        def settled(player):
            return (player.assault_mode_state is not None and not player.mode_script.data_in_flight
                    and len(player.assault_point_states) == 2
                    and all(stream.delivered and not stream.data_in_flight
                            for stream in player.assault_point_scripts.values()))

        def advance_to(now):
            run.now = now
            for link in clients:
                run.send(link)

        run.until(lambda: all(settled(player) for player in (attacker, defender)))
        for link in (attack_link, defend_link):
            ui, a, b = receive(link)
            self.assertIn((1, combat.WAITING_FOR_PLAYERS_STATE), ui.on())
            self.assertNotIn((0, escort.COUNTDOWN), ui.vars)
            self.assertEqual((a.vars[1, 0x06D9], b.vars[1, 0x06D9]), ("A", "B"))
            self.assertTrue(all(point.vars[1, assault.POINT_LOCKED] for point in (a, b)))
            self.assertTrue(all(point.vars[1, 0x3F75] is None for point in (a, b)))
            self.assertTrue(all(point.vars[0, 0x2DA4] for point in (a, b)))
            self.assertTrue(all(
                point.vars[0, escort.OBJECTIVE_KIND] == assault.POINT_OBJECTIVE for point in (a, b)
            ))
            placements = {escort.MAP_ENTITY | index for index in scene.placeables} - mode.outlines.keys()
            self.assertTrue(placements <= shown[link])
            self.assertFalse(shown[link] & mode.outlines.keys())
        self.assertTrue(match.start_custom_mode(run.now))
        self.assertFalse(match.start_custom_mode(run.now))
        for link, player in ((attack_link, attacker), (defend_link, defender)):
            run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: all(p.has_body and p.body_script is not None for p in (attacker, defender)))
        run.until(lambda: all(settled(p) and combat.ASSEMBLE_HEROES_STATE in p.assault_mode_state.states
                              for p in (attacker, defender)))
        for link in (attack_link, defend_link):
            ui, a, b = receive(link)
            self.assertIn((1, combat.ASSEMBLE_HEROES_STATE), ui.on())
            self.assertTrue(all(point.vars[1, 0x3F75] is None for point in (a, b)))

        advance_to(match.assemble_ends)
        run.until(lambda: all(settled(p) and p.assault_mode_state.instance_vars[59][0] == assault.Phase.SETUP
                              for p in (attacker, defender)))
        for link in (attack_link, defend_link):
            ui, a, b = receive(link)
            self.assertEqual(ui.vars[1, 59], assault.Phase.SETUP)
            self.assertTrue(all(point.vars[1, assault.POINT_LOCKED] for point in (a, b)))
            self.assertEqual(a.vars[1, 0x3F75], waypoint_type)
            self.assertIsNone(b.vars[1, 0x3F75])
        self.assertFalse(attacker.select_open)
        self.assertTrue(mode.doors)
        self.assertTrue(all(not mode.door_open(entity) for entity in mode.doors))
        first_doors = {entity for entity, door in mode.doors.items() if (0, assault.ATTACKERS) in door.rooms}
        first_shapes = {shape for entity in first_doors for shape in mode.room_collision[entity]}
        self.assertTrue(first_shapes)
        self.assertTrue(first_shapes <= set(mode.payload_collision.room_shapes))
        first_spawn = attacker.spawn
        advance_to(mode.deadline)
        run.until(lambda: all(p.assault_hud_bound and settled(p) and outline_a in p.assault_outline_states
                              for p in (attacker, defender)))
        self.assertTrue(first_shapes.isdisjoint(mode.payload_collision.room_shapes))
        for player, link, heading in ((attacker, attack_link, 0x10BE), (defender, defend_link, 0x10C0)):
            ui, a, b = receive(link)
            self.assertEqual(ui.vars[0, 0x0783], expr.Asset(0x0DE0000000000000 | heading))
            self.assertNotIn(0x1FEB, ui.graphs.values())  # the mode starts its own HUD
            self.assertIn((1, 17), ui.on())  # scoreboard permission
            self.assertEqual(ui.vars[0, escort.COUNTDOWN], 240.0)
            self.assertTrue({(1, index) for index in assault.PROGRESS_BINDINGS} <= ui.on())
            self.assertFalse(a.vars[1, assault.POINT_LOCKED])
            self.assertTrue(b.vars[1, assault.POINT_LOCKED])
            self.assertEqual(a.vars[1, 0x3F75], waypoint_type)
            self.assertIsNone(b.vars[1, 0x3F75])
            self.assertEqual(a.vars[1, 0x2F61], 3)
            self.assertEqual(a.vars[1, 0x3060], expr.f32(1 / 3))
            self.assertEqual(shown[link] & (mode.barriers.keys() | mode.overlays.keys()),
                             mode.room_visuals(player.team))
            self.assertEqual(shown[link] & mode.outlines.keys(), {outline_a})

        attacker.mover.position = defender.mover.position = mode.points[0].position
        advance_to(run.now + 2)
        run.step(8)
        _, a, _ = receive(attack_link)
        self.assertTrue(a.vars[0, 0x2690])
        self.assertEqual(a.vars[0, 0x266C], (expr.Entity(defender.body),))
        self.assertEqual(a.vars[0, 0x266D], (expr.Entity(attacker.body),))
        self.assertEqual((a.vars[0, 0x2688], a.vars[0, 0x2689]), (1, 1))
        self.assertEqual(a.vars[0, 0x268A], 0)
        defender.mover.position = defender.spawn[0]
        end = mode.deadline
        advance_to(run.now + mode.points[0].capture_seconds / 2)
        run.step(12)
        ui, a, _ = receive(attack_link)
        self.assertFalse(a.vars[1, assault.POINT_LOCKED])
        self.assertGreater(a.vars[0, 0x268A], 0.49)
        self.assertLess(a.vars[0, 0x268A], 0.6)
        self.assertGreater(ui.vars[1, 0x349C], 0.49)
        self.assertEqual(a.vars[0, 0x26AC], a.vars[0, 0x268A])
        self.assertEqual(a.vars[1, 0x3F75], waypoint_type)
        advance_to(run.now + mode.points[0].capture_seconds / 5)
        run.step(12)
        _, a, _ = receive(attack_link)
        self.assertGreaterEqual(a.vars[0, 0x268A], 2 / 3)
        self.assertEqual(a.vars[0, 0x26AC], a.vars[0, 0x268A])
        a_entity, b_entity = mode.entities
        send = run.send

        def a_retired():
            return attacker.assault_point_states[a_entity].presentation == (True, True)

        def withhold_retirement_ack(link):
            if link is not attack_link or not a_retired():
                send(link)

        with patch.object(run, "send", side_effect=withhold_retirement_ack):
            advance_to(run.now + mode.points[0].capture_seconds / 3)
            run.until(a_retired)
            run.step(6)
        self.assertTrue(attacker.assault_point_scripts[a_entity].data_in_flight)
        ui, a, b = receive(attack_link)
        self.assertEqual(ui.vars[0, 0x2937], expr.Entity(mode.entities[0]))
        self.assertIsNone(a.vars[1, 0x3F75])
        self.assertIsNone(b.vars[1, 0x3F75])
        self.assertFalse(shown[attack_link] & mode.outlines.keys())
        run.until(lambda: settled(attacker) and outline_b in attacker.assault_outline_states)
        ui, a, b = receive(attack_link)
        self.assertEqual(mode.stage, 1)
        self.assertEqual(mode.deadline, end + mode.points[1].round_seconds)
        self.assertNotEqual(attacker.spawn, first_spawn)
        self.assertTrue(a.vars[1, assault.POINT_COMPLETED])
        self.assertFalse(b.vars[1, assault.POINT_LOCKED])
        self.assertEqual(ui.vars[0, 0x2937], expr.Entity(mode.entities[1]))
        self.assertEqual(shown[attack_link] & (mode.barriers.keys() | mode.overlays.keys()),
                         mode.room_visuals(attacker.team))
        self.assertEqual(shown[attack_link] & mode.outlines.keys(), {outline_b})

        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (late,) = run.server.join_match(match, [(3, "Late", content.SOLDIER, 1, False)])
        late_link, late_player = run.connect(late)
        run.until(lambda: late_player.assault_hud_bound and settled(late_player)
                  and outline_b in late_player.assault_outline_states)
        late_ui, late_a, late_b = receive(late_link)
        self.assertEqual(late_ui.vars[1, 0x2934], 1)
        self.assertTrue(late_a.vars[1, assault.POINT_COMPLETED])
        self.assertFalse(late_b.vars[1, assault.POINT_LOCKED])
        self.assertIsNone(late_a.vars[1, 0x3F75])
        self.assertEqual(late_b.vars[1, 0x3F75], waypoint_type)
        self.assertEqual(late_player.spawn, content.spawn_point(scene, 1, 1, stage=1))
        self.assertEqual(shown[late_link] & mode.outlines.keys(), {outline_b})

        advance_to(mode.deadline - 4)
        run.step()
        attacker.mover.position = defender.mover.position = mode.points[1].position
        advance_to(mode.deadline)
        run.until(lambda: settled(attacker) and 131 in attacker.assault_mode_state.states)
        overtime, _, b = receive(attack_link)
        self.assertTrue(overtime.vars[1, 0x0D40])
        self.assertEqual(overtime.vars[0, escort.COUNTDOWN], 0.0)
        self.assertTrue(b.vars[0, 0x2690])

        defender.mover.position = defender.spawn[0]
        advance_to(run.now + mode.points[1].capture_seconds)
        run.until(lambda: all(
            settled(p) and p.assault_mode_state.instance_vars[59][0] == assault.Phase.COMPLETE
            for p in (attacker, defender, late_player)
        ))
        for link in (attack_link, defend_link, late_link):
            ui, a, b = receive(link)
            self.assertEqual(ui.vars[1, 59], assault.Phase.COMPLETE)
            self.assertEqual(ui.vars[0, 0x1D65], assault.ATTACKERS)
            self.assertTrue(all(point.vars[1, assault.POINT_COMPLETED] for point in (a, b)))
            self.assertTrue(all(point.vars[1, 0x3F75] is None for point in (a, b)))
            self.assertFalse(shown[link] & (mode.barriers.keys() | mode.overlays.keys()))
            self.assertFalse(shown[link] & mode.outlines.keys())
        full_round = [(a_entity, True), (a_entity, False), (b_entity, True), (b_entity, False)]
        self.assertEqual(markers, {attack_link: full_round, defend_link: full_round,
                                   late_link: [(b_entity, True), (b_entity, False)]})
        taken = list(map(expr.Entity, mode.entities))
        self.assertEqual(captures, {attack_link: taken, defend_link: taken, late_link: taken[1:]})
