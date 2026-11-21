"""Decode the native Control states over the encrypted game link."""

import struct
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import assault, collision, combat, contacts, content, control, escort, match_audio, world
from ow174.game.bits import BitReader
from ow174.game.script import decode, expr, graph
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick
from tests.runtime.test_match_audio import voice_packets

CONTROL_MAPS = (0x4B7, 0x662, 0x66D, 0x69E, 0x6B7, 0x7E2)
ILIOS = 0x080000000000066D
NEPAL = 0x08000000000004B7


class ControlRoundWireTests(unittest.TestCase):
    def test_nepal_door_healing_rims_and_enemy_panels_follow_authored_teams(self):
        scene = content.game_map(NEPAL, content.CONTROL_MODE)
        if scene is None:
            self.skipTest("the 104319 map catalog is required")
        scene = replace(scene, control=replace(scene.control, fixed_stage=2))
        run = ProtocolHarness()
        self.addCleanup(run.close)
        handoffs = run.server.create_match(
            scene, [(1, "Blue", content.SOLDIER, 0, False), (2, "Red", content.SOLDIER, 1, False)],
        )
        blue, red = [run.connect(handoff) for handoff in handoffs]
        run.step(10)
        mode = blue[1].match.control
        for (link, player), panels, signs, healing, rims in (
            (blue, {2031, 2035}, {2030, 2034}, {2956, 2957}, {2950, 2951}),
            (red, {2958, 2959}, {2954, 2955}, {2032, 2037}, {2033, 2036}),
        ):
            created = {record.entity & 0x7FFFFFFF: record for _, record in link.records
                       if record.op == world.OP_CREATE and record.entity in mode.overlays}
            self.assertEqual(set(created), rims | panels | signs | healing)
            for index in panels:
                record = created[index]
                entity = escort.MAP_ENTITY | index
                placement = mode.overlays[entity]
                self.assertEqual(record.create_definition, placement.placement_definition & 0xFFFFFFFF)
                pose = tuple(map(expr.f32, placement.position + placement.rotation))
                self.assertEqual(record.create_pose, pose)
                self.assertEqual(record.create_scale, tuple(map(expr.f32, placement.scale)))
                centre = contacts.add(placement.position, contacts.rotate(placement.rotation, (0, 2, 0)))
                normal = contacts.rotate(placement.rotation, (0, 0, 1))
                front, back = contacts.add(centre, normal), contacts.sub(centre, normal)
                walls = contacts.StaticWorld(mode.room_collision[entity])
                for team in (0, 1):
                    hit = contacts.scene_sweep(walls, front, back, ((0, 0, 0),), 0.0,
                                               collision.mover_category(team, False))
                    self.assertEqual(hit is not None, team == player.team)
            self.assertTrue(all(not hasattr(created[index], "create_definition") for index in rims))

    def test_six_variants_use_the_authored_areas_and_both_teams_spawns(self):
        if content.game_map(ILIOS, content.CONTROL_MODE) is None:
            self.skipTest("the 104319 map catalog is required")
        for index in CONTROL_MAPS:
            with self.subTest(map=index):
                scene = content.game_map(0x0800000000000000 | index, content.CONTROL_MODE)
                self.assertIsNotNone(scene.control)
                self.assertEqual(len(scene.control.points), 3)
                self.assertTrue(all(point.outlines and point.submap_name for point in scene.control.points))
                self.assertTrue(all(box[:4] != (0, 0, 0, 1) or box[7:] != (5, 3, 5)
                                    for point in scene.control.points for box in point.boxes))
                doors = {door.index for door in scene.control.room_placeables if door.door}
                overlays = [room for room in scene.control.room_placeables if room.viewers]
                self.assertTrue(doors.issubset(scene.placeables))
                self.assertFalse({room.index for room in overlays}.intersection(scene.placeables))
                self.assertTrue(any(room.collision for room in overlays))
                mode = control.ControlRound(scene.control)
                self.assertEqual(set(mode.healing_rooms), {
                    (stage, team) for stage in range(3) for team in (0, 1)
                })
                for team in (0, 1):
                    locations = {content.spawn_point(scene, team, 0, stage=stage) for stage in range(3)}
                    self.assertEqual(len(locations), 3)
                    for stage in range(3):
                        position, _ = content.spawn_point(scene, team, 0, stage=stage)
                        self.assertTrue(assault.inside_boxes(mode.healing_rooms[stage, team], position))

    def test_fixed_submap_closes_doors_again_and_heals_only_its_own_team(self):
        scene = content.game_map(ILIOS, content.CONTROL_MODE)
        if scene is None or not graph.graph(escort.DOOR_GRAPH):
            self.skipTest("the 104319 Control assets are required")
        scene = replace(scene, control=replace(scene.control, fixed_stage=2, capture_scale=2, score_scale=5))
        run = ProtocolHarness()
        self.addCleanup(run.close)
        with patch.object(run.server.environment, "randbelow") as rng:
            blue_handoff, red_handoff = run.server.create_match(
                replace(scene, custom_start=content.START_IMMEDIATELY),
                [(1, "Blue", content.SOLDIER, 0, False), (2, "Red", content.SOLDIER, 1, False)],
            )
            rng.assert_not_called()
        blue_link, blue = run.connect(blue_handoff)
        red_link, red = run.connect(red_handoff)
        match = blue.match
        mode = match.control

        def jump(now):
            run.now = now
            for link in (blue_link, red_link):
                run.send(link)
            run.step()

        self.assertEqual(mode.order, (2, 2, 2))
        for link, player in ((blue_link, blue), (red_link, red)):
            self.assertEqual(player.spawn, content.spawn_point(scene, player.team, 0, stage=2))
            run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: blue.spawned and red.spawned)
        blue_health = match.combat.body_vitals(blue.body, blue)
        red_health = match.combat.body_vitals(red.body, red)
        blue.mover.position = red.mover.position = blue.spawn[0]
        blue_health.damage(80)
        red_health.damage(80)
        blue_before, red_before = blue_health.total, red_health.total
        run.step(5)
        self.assertAlmostEqual(blue_health.total - blue_before, 400 * 0.016 * 5)
        self.assertEqual(red_health.total, red_before)
        blue.mover.position = red.mover.position = mode.points[2].boxes[0][4:7]

        def received_health(link, player):
            pool = link.fields.get(player.body, {}).get(51, {}).get(0)
            return None if pool is None else struct.unpack_from("<f", pool, 8)[0]

        viewers = ((blue_link, blue, blue_health), (red_link, red, red_health))
        run.until(lambda: all(
            (health := received_health(link, player)) is not None and abs(health - vitals.total) < 1e-4
            for link, player, vitals in viewers
        ))

        door_entity = next(entity for entity, placement in mode.doors.items() if placement.rooms[0][0] == 2)
        jump(match.assemble_ends)
        self.assertEqual(mode.phase, escort.Phase.SETUP)
        jump(mode.deadline)
        self.assertEqual(mode.phase, escort.Phase.UNLOCK)
        run.until(lambda: blue.escort_door_states[door_entity].pose == escort.DoorPose.OPEN, limit=300)

        blue.mover.position = mode.points[2].boxes[0][4:7]
        red.mover.position = red.spawn[0]
        jump(mode.deadline + 4)
        self.assertEqual(mode.owner, 0)
        jump(run.now + 24)
        self.assertEqual(mode.phase, escort.Phase.ROUND_END)
        jump(mode.deadline)
        self.assertEqual(mode.phase, escort.Phase.COMPLETE)
        jump(mode.deadline)
        self.assertEqual((mode.round_index, mode.stage, mode.phase), (1, 2, escort.Phase.SETUP))
        run.until(lambda: blue.escort_door_states[door_entity].pose == escort.DoorPose.CLOSED)
        self.assertFalse(mode.door_open(door_entity))
        door = decode.ClientBody()
        cursor = 0

        def closed_on_wire():
            nonlocal cursor
            for _, record in blue_link.records[cursor:]:
                if record.entity == door_entity and record.chunk is not None:
                    header = BitReader(record.chunk.getvalue())
                    if decode.w_u32(header) + decode.w_var(header) > door.applied:
                        door.apply(record)
            cursor = len(blue_link.records)
            return (1, escort.DOOR_CLOSE_ALIAS) in door.on() and (1, escort.DOOR_OPEN_ALIAS) not in door.on()

        run.until(closed_on_wire)

    def test_two_teams_overtime_round_change_and_late_join(self):
        scene = content.game_map(ILIOS, content.CONTROL_MODE)
        if (scene is None or scene.control is None or not collision.cache_path(ILIOS).is_file()
                or not all(graph.graph(index) for index in (
                    control.MODE_GRAPH, control.MODE_HUD, control.ROUND_RESULT, assault.POINT_GRAPH,
                    match_audio.CAPTURE_SOUND_GRAPH, control.MATCH_RESULT_INTRO,
                ))):
            self.skipTest("the 104319 Control assets are required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        geometry = collision._read_cache(ILIOS, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        with patch.object(run.server.environment, "randbelow", side_effect=(0, 0)):
            blue, red = run.server.create_match(
                replace(scene, custom_start=content.START_MANUALLY),
                [(1, "Blue", content.SOLDIER, 0, False), (2, "Red", content.SOLDIER, 1, False)],
            )
        blue_link, blue = run.connect(blue)
        red_link, red = run.connect(red)
        match = blue.match
        mode = match.control
        mode_entity = world.GAME_MODE_ENTITY
        door = next(entity for entity, placement in mode.doors.items() if placement.rooms[0][0] == 0)
        watched = (mode_entity, *mode.entities, door)
        views = {}
        actions = {}
        score_effects = {}
        unlock_effects = {}
        setup_actions = {}
        assembly_music = {}
        setup_music = {}
        slow_motion = {}
        match_music = {}
        round_actions = {}
        match_intro = {}
        room_visibility = {}
        room_frames = {}
        setup_music_action = (match_audio.CAPTURE_SOUND_INSTANCE, match_audio.CAPTURE_SOUND_ACTION)

        def receive(link):
            state = views.setdefault(link, {entity: decode.ClientBody() for entity in watched})
            effects = actions.setdefault(link, [])
            visible = room_visibility.setdefault(link, set())
            frames = room_frames.setdefault(link, {})
            for _, record in link.records:
                if record.entity in mode.overlays:
                    if record.op == world.OP_CREATE:
                        visible.add(record.entity)
                    elif record.op == world.OP_DESTROY:
                        visible.discard(record.entity)
                        frames.pop(record.entity, None)
                    if record.chunk is not None and record.entity in visible:
                        view = frames.setdefault(record.entity, decode.ClientBody())
                        view.apply(record)
                if record.entity not in state or record.chunk is None:
                    continue
                view = state[record.entity]
                header = BitReader(record.chunk.getvalue())
                first = decode.w_u32(header)
                if first + decode.w_var(header) <= view.applied:
                    continue
                if record.entity == mode_entity and not view.applied:
                    self.assertTrue(all(state[point].applied for point in mode.entities))
                frame = view.apply(record)
                if record.entity == mode_entity:
                    self.assertEqual(view.graphs[1], control.MODE_GRAPH)
                    self.assertNotIn(combat.MODE_UI, view.graphs.values())
                    self.assertNotIn(control.MODE_HUD, view.graphs.values())
                    if (intro := frame.instances.get(control.MATCH_RESULT_INTRO_INSTANCE)) is not None:
                        active, pulse = intro.states[control.MATCH_RESULT_INTRO_PULSE]
                        self.assertTrue(active)
                        match_intro.setdefault(link, []).append(pulse)
                    if 1 in frame.instances:
                        states = frame.instances[1].states
                        if (slow := states.get(control.ROUND_END_SLOW_MOTION)) and slow[1]:
                            slow_motion.setdefault(link, []).append(slow)
                        if (music := states.get(control.MATCH_RESULT_MUSIC)) and music[0]:
                            match_music.setdefault(link, []).append(music[1])
                    effects.extend(view.vars[1, 0x1332] for _, instance, node in frame.actions
                                   if (instance, node) == (1, control.CAPTURE_EFFECT))
                    for stamp, instance, node in frame.actions:
                        if (instance, node) == setup_music_action:
                            self.assertEqual(view.vars[instance, match_audio.CAPTURE_EFFECT_VAR],
                                             expr.Asset(control.SETUP_MUSIC_EFFECT))
                            setup_music.setdefault(link, []).append(view.vars[1, 0x0517])
                        if instance != 1:
                            continue
                        if node in (control.ROUND_RESULT_CUE, control.INTERMISSION_MUSIC):
                            round_actions.setdefault(link, []).append(node)
                        if node == control.ROUND_RESULT_CUE:
                            final_score = control.SCORE_EFFECTS[view.vars[1, 0x1E4F]]
                            self.assertIn((stamp, 1, final_score), frame.actions)
                            self.assertIn((stamp, 1, control.INTERMISSION_MUSIC), frame.actions)
                        if node == control.UNLOCK_EFFECT:
                            unlock_effects.setdefault(link, []).append(view.vars[1, 0x0517])
                        elif node == control.ASSEMBLY_MUSIC:
                            assembly_music.setdefault(link, []).append(view.vars[1, 0x0517])
                        elif node in control.SETUP_COUNTDOWN.values():
                            setup_actions.setdefault(link, []).append(node)
                        elif node in control.SCORE_EFFECTS:
                            score_key = 0x1330 + control.SCORE_EFFECTS.index(node)
                            score_effects.setdefault(link, []).append((node, view.vars[1, score_key]))
                self.assertLessEqual(sum(state[point].vars.get((1, 0x3F75)) == control.POINT_MARKER
                                         for point in mode.entities), 1)
            link.records.clear()
            return state[mode_entity], tuple(state[entity] for entity in mode.entities)

        def settled(player, phase, stage=0):
            state = player.control_mode_state
            return (state is not None and (state.phase, state.stage) == (phase, stage)
                    and not player.mode_script.data_in_flight and len(player.assault_point_states) == 3
                    and not any(stream.data_in_flight for stream in player.assault_point_scripts.values()))

        def wait(phase, stage=0, players=(blue, red)):
            run.until(lambda: all(settled(player, phase, stage) for player in players))

        def jump(now):
            run.now = now
            for link in run.game.links.values():
                run.send(link)
            run.step()

        def voice_lines(link):
            return [row["+0x90"] & 0xFFFF for row in voice_packets(link)
                    if row["+0x78"]["+0x0"] == mode_entity]

        phase = control.Phase
        wait(phase.WAITING)
        for link, player in ((blue_link, blue), (red_link, red)):
            ui, points = receive(link)
            self.assertEqual(room_visibility[link], mode.room_visuals(player.team))
            self.assertEqual(set(room_frames[link]), room_visibility[link])
            self.assertTrue(all(view.graphs[1] == escort.ROOM_OVERLAY_GRAPH and (1, 3) in view.on()
                                for view in room_frames[link].values()))
            self.assertEqual(ui.vars[1, 59], control.ControlPhase.WAITING)
            self.assertNotIn((0, escort.COUNTDOWN), ui.vars)
            self.assertFalse(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
            self.assertTrue(all(point.vars[1, assault.POINT_LOCKED] for point in points))
            self.assertTrue(all(point.vars[1, 0x3F75] is None for point in points))
        self.assertTrue(match.start_custom_mode(run.now))
        for link, player in ((blue_link, blue), (red_link, red)):
            run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: all(player.has_body and player.body_script is not None for player in (blue, red)))
        wait(phase.ASSEMBLE)
        jump(match.assemble_ends - 10)
        run.step(4)
        for link in (blue_link, red_link):
            ui, _ = receive(link)
            self.assertEqual(ui.vars[0, 59], 1)
            self.assertEqual(assembly_music[link], [10.0])
        jump(match.assemble_ends)
        wait(phase.SETUP)
        ui, points = receive(blue_link)
        self.assertEqual(ui.vars[0, 59], 2)  # 1FEE reads the entity-scoped phase.
        self.assertIn((1, 15), ui.on())
        self.assertNotIn((1, 84), ui.on())
        self.assertEqual([point.vars[1, 0x3F75] for point in points], [control.POINT_MARKER, None, None])
        self.assertTrue(points[0].vars[1, assault.POINT_LOCKED])
        self.assertFalse(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
        self.assertFalse(any(mode.door_open(entity) for entity in mode.doors))
        self.assertFalse(views[blue_link][door].vars[1, 0x22A5])
        for seconds in (10, 9, 5, 4, 3, 2, 1):
            jump(mode.deadline - seconds)
            run.step(4)
        setup_lines = [match_audio.NUMBERS[second][1] for second in range(5, 0, -1)]
        for link in (blue_link, red_link):
            receive(link)
            self.assertEqual(voice_lines(link)[-5:], setup_lines)
            self.assertEqual(setup_actions[link], list(control.SETUP_COUNTDOWN.values()))
            self.assertEqual(setup_music[link], [10.0])
            self.assertEqual(assembly_music[link], [10.0])
            self.assertNotIn(link, unlock_effects)
        jump(mode.deadline)
        wait(phase.UNLOCK)
        run.until(lambda: all(p.escort_door_states[door].pose != escort.DoorPose.CLOSED
                              and not p.escort_door_scripts[door].data_in_flight for p in (blue, red)))
        ui, points = receive(blue_link)
        self.assertEqual(ui.vars[0, 59], 3)
        self.assertNotIn(match_audio.CAPTURE_SOUND_INSTANCE, ui.graphs)
        self.assertNotIn((1, 15), ui.on())
        self.assertIn((1, 84), ui.on())
        self.assertTrue(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
        self.assertEqual(points[0].vars[1, 0x3F75], control.POINT_MARKER)
        self.assertTrue(points[0].vars[1, assault.POINT_LOCKED])
        self.assertTrue(all(mode.door_open(entity) == (placement.rooms[0][0] == 0)
                            for entity, placement in mode.doors.items()))
        self.assertTrue(views[blue_link][door].vars[1, 0x22A5])
        run.until(lambda: all(voice_lines(link)[-1:] == [0x7B53] for link in (blue_link, red_link)))
        start_lines = {link: voice_lines(link) for link in (blue_link, red_link)}
        for seconds in (20, 10, 5, 4, 3.25, 3, 2, 1):
            jump(mode.deadline - seconds)
            run.step(4)
            for link in (blue_link, red_link):
                receive(link)
                self.assertEqual(voice_lines(link), start_lines[link])
            if seconds > 3:
                self.assertFalse(unlock_effects)
        self.assertEqual(unlock_effects, {blue_link: [3.0], red_link: [3.0]})
        # Leave Blue's last clock frame unacknowledged across activation. The
        # objective update and announcement should still be sent on the unlock tick.
        run.now = mode.deadline - 0.75
        run.step(ack_except=(blue_link,))
        self.assertTrue(blue.mode_script.data_in_flight)
        run.now = mode.deadline
        run.step(ack_except=(blue_link,))
        ui, points = receive(blue_link)
        self.assertTrue(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
        self.assertFalse(points[0].vars[1, assault.POINT_LOCKED])
        self.assertEqual(voice_lines(blue_link), start_lines[blue_link] + [match_audio.CONTROL_UNLOCKED[1]])
        self.assertEqual(voice_lines(red_link), start_lines[red_link] + [match_audio.CONTROL_UNLOCKED[1]])
        run.send(blue_link)
        wait(phase.ACTIVE)
        center = mode.points[0].boxes[0][4:7]
        blue.mover.position = red.mover.position = center
        run.step(8)
        ui, points = receive(red_link)
        self.assertFalse(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
        self.assertTrue(points[0].vars[0, 0x2690])
        self.assertFalse(points[0].vars[1, assault.POINT_LOCKED])
        self.assertEqual(points[0].vars[0, 0x266C], (expr.Entity(blue.body),))
        red.mover.position = red.spawn[0]
        original_send = run.send

        def blue_point_changed():
            return blue.assault_point_states[mode.entities[0]].owner == 0

        def withhold_point_ack(link):
            if link is not blue_link or not blue_point_changed():
                original_send(link)

        with patch.object(run, "send", side_effect=withhold_point_ack):
            jump(run.now + 8.1)
            run.until(blue_point_changed)
            run.step(6)
        ui, points = receive(blue_link)
        self.assertEqual(points[0].vars[0, 0x0063], 0)
        self.assertEqual(ui.vars[1, 0x1332], control.NEUTRAL)
        self.assertEqual(actions[blue_link], [])
        run.until(lambda: all(player.control_mode_state.owner == 0 for player in (blue, red)))
        wait(phase.ACTIVE)
        for link in (blue_link, red_link):
            ui, points = receive(link)
            self.assertEqual(ui.vars[1, 0x1332], 0)
            self.assertEqual(points[0].vars[0, 0x0063], 0)
            self.assertEqual(actions[link], [0])
            self.assertIn((1, control.SCORE_SOUND_SCOPE), ui.on())
        blue.mover.position = blue.spawn[0]
        origin = run.now - mode.scores[0] / mode.score_rate
        jump(origin + 74 / mode.score_rate)
        run.step(5)
        for link in (blue_link, red_link):
            receive(link)
            self.assertNotIn(link, score_effects)
        for score in (75.25, 76.25, 82.25):
            jump(origin + score / mode.score_rate)
            run.step(5)
        for link in (blue_link, red_link):
            receive(link)
            expected = [(control.SCORE_EFFECTS[0], score) for score in (75.25, 76.25, 82.25)]
            self.assertEqual(score_effects[link], expected)
        jump(origin + 118)
        red.mover.position = center
        run.step(2)
        red.mover.position = red.spawn[0]
        jump(run.now + 2)
        wait(phase.ACTIVE)
        ui, _ = receive(blue_link)
        self.assertTrue(ui.vars[1, 0x0D40])
        self.assertTrue(99 <= ui.vars[1, 0x1330] < 100)
        viewers = ((blue_link, blue), (red_link, red))
        for link, player in viewers:
            player.mover.position = list(player.mover.position)
            run.command(link)
        jump(mode.overtime_end)
        wait(phase.ROUND_END)
        for (link, player), banner, line in zip(viewers, ((1, 0), (0, 1)), (0x77BE, 0x77BC), strict=True):
            ui, points = receive(link)
            self.assertEqual(ui.vars[0, 59], 4)
            self.assertEqual(ui.vars[0, 0x09C3], (1, 0))
            self.assertEqual((ui.vars[0, 0x1846], ui.vars[0, 0x1847]), banner)
            self.assertEqual(ui.vars[1, 0x1330], 100)
            self.assertEqual(ui.vars[1, 0x1332], control.NEUTRAL)
            self.assertEqual(ui.vars[0, 0x1332], control.NEUTRAL)
            self.assertIn((1, control.OBJECTIVE_HUD_LIVE), ui.on())
            self.assertFalse(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
            self.assertIsNone(ui.vars[1, 0x1D65])
            self.assertEqual(ui.vars[1, 0x22FC], 0.375)
            self.assertNotIn(4, ui.graphs)
            self.assertIsNone(points[0].vars[1, 0x3F75])
            self.assertTrue(points[0].vars[1, assault.POINT_LOCKED])
            self.assertEqual(round_actions[link], [control.ROUND_RESULT_CUE, control.INTERMISSION_MUSIC])
            self.assertNotIn(link, match_music)
            self.assertEqual(slow_motion[link], [(True, {"which": 1, "value": player.commands.last_frame})])
            self.assertEqual(voice_lines(link)[-1], line)
            with patch.object(player.mover, "step", wraps=player.mover.step) as step:
                run.command(link)
                self.assertAlmostEqual(step.call_args.args[1], player.quanta / 1e6 * 0.375)
        jump(mode.deadline - 0.25)
        wait(phase.ROUND_END)
        for link, _ in viewers:
            ui, points = receive(link)
            self.assertNotIn(4, ui.graphs)
            self.assertIsNone(points[0].vars[1, 0x3F75])
            self.assertIn((1, control.ROUND_END_SLOW_MOTION), ui.on())
        jump(mode.deadline)
        wait(phase.COMPLETE)
        receive(red_link)
        ui, points = receive(blue_link)
        self.assertFalse(room_visibility[blue_link])
        self.assertFalse(room_visibility[red_link])
        self.assertEqual(ui.graphs[4], control.ROUND_RESULT)
        self.assertEqual(ui.vars[0, 59], 4)
        self.assertEqual(ui.vars[0, 0x09C3], (1, 0))
        self.assertEqual(ui.vars[4, 0x1D65], 0)
        self.assertEqual(ui.vars[1, 0x1330], 100)
        self.assertEqual(ui.vars[1, 0x1332], control.NEUTRAL)
        self.assertIn((1, control.OBJECTIVE_HUD_LIVE), ui.on())
        self.assertFalse(ui.vars[0, control.UNLOCK_CLOCK_VISIBLE])
        self.assertTrue(all(point.vars[1, 0x3F75] is None for point in points))
        self.assertEqual(score_effects[blue_link][-1], (control.SCORE_EFFECTS[0], 100.0))
        self.assertEqual(round_actions[blue_link], [control.ROUND_RESULT_CUE, control.INTERMISSION_MUSIC])
        self.assertNotIn(blue_link, match_music)
        self.assertNotIn(blue_link, match_intro)
        self.assertEqual(slow_motion[blue_link][-1], (False, {"which": 0, "value": blue.commands.last_frame}))
        self.assertNotIn((1, control.ROUND_END_SLOW_MOTION), ui.on())
        with patch.object(blue.mover, "step", wraps=blue.mover.step) as step:
            run.command(blue_link)
            self.assertEqual(step.call_args.args[1], blue.quanta / 1e6)
        run.until(lambda: all(p.match_audio.follow_up and p.match_audio.follow_up.due is not None
                              for p in (blue, red)))
        jump(max(p.match_audio.follow_up.due for p in (blue, red)))
        run.until(lambda: all(p.match_audio.follow_up and p.match_audio.follow_up.due is not None
                              for p in (blue, red)))
        jump(max(p.match_audio.follow_up.due for p in (blue, red)))
        score_lines = {blue_link: [0x8447, 0x8408, 0x8431], red_link: [0x8447, 0x8427, 0x8426]}
        run.until(lambda: all(voice_lines(link)[-3:] == lines for link, lines in score_lines.items()))
        self.assertEqual(mode.stage, 0)

        old_body = blue.body
        run.now = mode.deadline
        run.step(ack_except=(blue_link,))
        self.assertTrue(blue.client.own_possession_pending)
        self.assertEqual(blue.control_mode_state.phase, phase.COMPLETE)
        ui, points = receive(blue_link)
        self.assertIn(4, ui.graphs)
        self.assertIn((1, control.OBJECTIVE_HUD_LIVE), ui.on())
        self.assertTrue(all(point.vars[1, 0x3F75] is None for point in points))
        run.send(blue_link)
        wait(phase.SETUP, 1)
        self.assertFalse(match.assembling())
        self.assertFalse(blue.select_open)
        self.assertNotEqual(blue.body, old_body)
        self.assertEqual(blue.spawn, content.spawn_point(scene, 0, 0, stage=1))
        for actual, expected in zip(blue.mover.position, blue.spawn[0], strict=True):
            self.assertAlmostEqual(actual, expected, delta=1 / 1024)
        ui, points = receive(blue_link)
        self.assertEqual(ui.vars[1, 59], control.ControlPhase.SETUP)
        self.assertNotIn((1, 10), ui.on())
        self.assertNotIn(4, ui.graphs)
        self.assertEqual(ui.vars[1, 0x27A2], 1)
        self.assertEqual(ui.vars[1, 0x1332], control.NEUTRAL)
        self.assertEqual(ui.vars[0, 0x09C3], (1, 0))
        self.assertNotIn((1, control.OBJECTIVE_HUD_LIVE), ui.on())
        self.assertEqual([point.vars[1, 0x3F75] for point in points], [None, control.POINT_MARKER, None])
        self.assertTrue(points[1].vars[1, assault.POINT_LOCKED])
        self.assertTrue(all(not mode.door_open(entity)
                            for entity, placement in mode.doors.items() if placement.rooms[0][0] == 1))
        jump(mode.deadline - 10)
        wait(phase.SETUP, 1)
        for link in (blue_link, red_link):
            receive(link)
            viewer = blue if link is blue_link else red
            self.assertEqual(room_visibility[link], mode.room_visuals(viewer.team))
            self.assertEqual(setup_music[link], [10.0, 10.0])
            self.assertEqual(assembly_music[link], [10.0])
        jump(mode.deadline)
        wait(phase.UNLOCK, 1)
        ui, points = receive(blue_link)
        self.assertEqual([point.vars[1, 0x3F75] for point in points], [None, control.POINT_MARKER, None])
        jump(mode.deadline)
        wait(phase.ACTIVE, 1)
        blue.mover.position = mode.points[1].boxes[0][4:7]
        jump(run.now + 8.1)
        jump(run.now + 30)

        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (handoff,) = run.server.join_match(match, [(3, "Late", content.SOLDIER, 1, False)])
        late_link, late = run.connect(handoff)
        wait(phase.ACTIVE, 1, (late,))
        ui, points = receive(late_link)
        self.assertEqual(room_visibility[late_link], mode.room_visuals(late.team))
        self.assertEqual(late.spawn, content.spawn_point(scene, 1, 1, stage=1))
        self.assertEqual(ui.vars[1, 0x27A2], 1)
        self.assertGreaterEqual(ui.vars[1, 0x1330], 25)
        self.assertEqual(points[1].vars[1, 0x3F75], control.POINT_MARKER)
        self.assertEqual(actions[late_link], [])
        self.assertNotIn(late_link, setup_music)
        self.assertNotIn(late_link, unlock_effects)
        self.assertNotIn(match_audio.CONTROL_UNLOCKED[1], voice_lines(late_link))

        viewers = ((blue_link, True), (red_link, False), (late_link, False))
        jump(mode.last_update + (99.3 - mode.scores[0]) / mode.score_rate)
        wait(phase.ACTIVE, 1, (blue, red, late))
        last_scores = {}
        for link, _ in viewers:
            ui, _ = receive(link)
            last_scores[link] = ui.vars[1, 0x1330]
            self.assertEqual(int(last_scores[link]), 99)
        jump(run.now + 2)
        wait(phase.COMPLETE, 1, (blue, red, late))
        for link, won in viewers:
            ui, points = receive(link)
            self.assertEqual(ui.vars[1, 59], control.ControlPhase.RESULT)
            self.assertEqual(ui.vars[0, 0x09C3], (2, 0))
            self.assertEqual(ui.vars[0, 0x1D65], 0)
            self.assertTrue(ui.vars[0, 0x4C46])
            self.assertFalse(ui.vars[0, 0x4C47])
            self.assertEqual(ui.vars[1, 0x1330], last_scores[link])
            self.assertEqual(ui.vars[0, 0x4C73], (last_scores[link], 0))
            self.assertNotIn((1, control.OBJECTIVE_HUD_LIVE), ui.on())
            self.assertIn((2, control.MATCH_RESULT_HUD_OFF), ui.on())
            self.assertNotIn(4, ui.graphs)
            self.assertNotIn((1, control.ROUND_END_SLOW_MOTION), ui.on())
            self.assertTrue(all(point.vars[1, 0x3F75] is None for point in points))
            intro_id = control.MATCH_RESULT_INTRO_INSTANCE
            self.assertEqual(ui.graphs[intro_id], control.MATCH_RESULT_INTRO)
            self.assertEqual(ui.vars[intro_id, 0x0918], expr.Map(((0x1330, 2), (0x1331, 0))))
            self.assertEqual(match_intro[link], [{"fresh": True, "count": 1}])
            self.assertEqual(match_music[link], [{"fresh": True, "count": 1}])
            self.assertEqual(voice_lines(link)[-1], match_audio.RESULT[won][1])
        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (handoff,) = run.server.join_match(match, [(4, "Late result", content.SOLDIER, 1, False)])
        end_link, end_viewer = run.connect(handoff)
        wait(phase.COMPLETE, 1, (end_viewer,))
        ui, _ = receive(end_link)
        self.assertEqual(ui.vars[0, 0x1D65], 0)
        self.assertEqual(ui.vars[1, 0x1330], 99)
        self.assertIn((2, control.MATCH_RESULT_HUD_OFF), ui.on())
        self.assertEqual(match_music[end_link], [{"fresh": False, "count": 1}])
        self.assertEqual(match_intro[end_link], [{"fresh": False, "count": 1}])
        self.assertNotIn(match_audio.RESULT[False][1], voice_lines(end_link))
