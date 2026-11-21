"""Drive the Rialto round through the loopback game link and decode what clients receive."""

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import collision, combat, contacts, content, escort, hybrid, statescript, world
from ow174.game.bits import BitReader
from ow174.game.commands import Command
from ow174.game.match import SEND_EVERY
from ow174.game.script import decode, expr, graph
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick

RIALTO = 0x0800000000000871
GIBRALTAR = 0x0800000000000184


class EscortRoundWireTests(unittest.TestCase):
    def test_team_switch_refreshes_checkpoint_rows_only_after_the_team_update_arrives(self):
        scene = content.game_map(GIBRALTAR, content.ESCORT_MODE)
        if scene is None or scene.escort is None or graph.graph(escort.PAYLOAD_GRAPH) is None:
            self.skipTest("Gibraltar rail and payload graph are required")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        attack, defense = run.server.create_match(
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Switching", content.SOLDIER, 1, False), (2, "Staying", content.SOLDIER, 0, False)],
        )
        link, player = run.connect(attack)
        other, defender = run.connect(defense)
        match = player.match
        mode = match.escort
        run.until(lambda: player.escort_payload_script.delivered and defender.escort_payload_script.delivered)
        mode.phase = escort.Phase.ACTIVE
        mode.last_update = run.now
        mode.deadline = run.now + 180
        mode._push(mode.checkpoints[0][0], run.now)
        self.assertEqual(mode.stage, 1)

        def settled(peer):
            return (peer.escort_mode_state.stage == 1 and peer.escort_payload_state.active
                    and not peer.escort_payload_script.data_in_flight and not peer.mode_script.data_in_flight)

        def read_updates(peer, model):
            changes = []
            for _, record in peer.records:
                if record.entity == mode.payload and record.chunk is not None:
                    frame = model.apply(record)
                    if frame is not None and 1 in frame.instances:
                        fraction_update = frame.instances[1].vars
                        if escort.PAYLOAD_CHECKPOINT_FRACTIONS in fraction_update:
                            changes.append(model.vars[1, escort.PAYLOAD_CHECKPOINT_FRACTIONS])
            peer.records.clear()
            return changes

        run.until(lambda: settled(player) and settled(defender))
        cart, other_cart = decode.ClientBody(), decode.ClientBody()
        fractions = read_updates(link, cart)
        self.assertEqual(len(fractions), 1)
        self.assertEqual(read_updates(other, other_cart), fractions)

        self.assertTrue(match.move_custom_player(player, escort.DEFENDERS))
        run.step(12, ack_except=(link,))
        self.assertIsNotNone(player.escort_hud_team_update)
        self.assertEqual(read_updates(link, cart), [])
        run.send(link)
        run.until(lambda: not player.escort_payload_state.checkpoint_fractions, ack_except=(link,))
        run.step(12, ack_except=(link,))
        self.assertEqual(read_updates(link, cart), [()])
        self.assertTrue(player.escort_payload_script.data_in_flight)
        self.assertIs(cart.vars[0, escort.PAYLOAD_ACTIVE], True)
        self.assertEqual(cart.vars[0, escort.CHECKPOINT_INDEX], 1)
        run.send(link)
        run.until(lambda: player.escort_payload_state.checkpoint_fractions and settled(player))
        self.assertEqual(read_updates(link, cart), fractions)
        self.assertEqual(read_updates(other, other_cart), [])
        self.assertEqual(mode.stage, 1)

    def test_authored_route_stops_and_automatic_passages(self):
        for index, mode_id, wait in ((0x2C3, content.ESCORT_MODE, 5.0),
                                    (0x68D, content.HYBRID_MODE, 10.0),
                                    (0x7F4, content.HYBRID_MODE, 10.0)):
            with self.subTest(map=f"{index:04X}"):
                self._check_authored_route(index, mode_id, wait)

    def _check_authored_route(self, index, mode_id, wait):
        scene = content.game_map(0x0800000000000000 | index, mode_id)
        if scene is None or not (scene.hybrid or scene.escort):
            self.skipTest("Requires local 104319 Escort and Hybrid routes")
        speed_scale = 1.5
        mode = (hybrid.HybridRound(scene.hybrid, speed_scale=speed_scale) if scene.hybrid
                else escort.EscortRound(scene.escort, speed_scale=speed_scale))
        if scene.hybrid:
            mode.capture.captured = 1
        mode.phase, mode.deadline, mode.last_update = escort.Phase.ACTIVE, 900.0, 100.0
        start, cue = next((distance, point) for distance, point in mode.route_events
                          if point.auto_drive_to is not None)
        endpoint = mode.route_distances[cue.auto_drive_to]

        def push_to(distance):
            for _ in range(200):
                if mode.distance >= distance:
                    return
                position = mode.movement().position
                mode.advance(mode.last_update + 1, ((101, escort.ATTACKERS, position),))
            self.fail(f"Payload stopped at {mode.distance:.3f} before {distance:.3f}")

        if scene.hybrid:
            small_stop, small = mode.route_events[0]
            push_to(small_stop)
            self.assertEqual((small.wait_seconds, mode.stage), (7.0, 0))
            self.assertEqual(mode.route_wait_until, mode.last_update + 7)
            self.assertEqual(mode.payload_state().pushers, ())
        client = decode.ClientBody()
        stream = statescript.Stream(mode.payload)
        previous = mode.payload_state()
        client.apply(stream.full_frame(previous.frame(None, now=mode.last_update)))

        def cart_delta():
            nonlocal previous
            state = mode.payload_state()
            stream.delivered = stream.last
            (record,) = stream.reliable_frames(state.frame(previous, now=mode.last_update), 4096,
                                                full=False)
            previous = state
            return client.apply(record)

        push_to(start)
        arrived = mode.last_update
        self.assertEqual(mode.distance, start)
        self.assertEqual(mode.stage, 1 if scene.hybrid else 2)
        self.assertEqual(mode.route_wait_until, arrived + wait)
        frame = cart_delta()
        self.assertTrue(client.vars[0, escort.PAYLOAD_ACTIVE])
        self.assertEqual(client.vars[1, escort.PAYLOAD_PUSHERS], ())
        if scene.hybrid:
            impact = escort.PAYLOAD_GATE_IMPACT_INSTANCE
            self.assertEqual(frame.instances[impact].states[0], (True, {"counter": 1, "time": 0}))
            mode.advance(arrived + 2, ())
            late = decode.ClientBody()
            baseline = mode.payload_state().frame(None, now=mode.last_update)
            late.apply(statescript.Stream(mode.payload).full_frame(baseline))
            self.assertIn((impact, 0), late.on())

        speed = escort.AUTO_DRIVE_SPEED * speed_scale
        position = mode.movement().position
        participants = [(101, escort.ATTACKERS, position), (102, escort.DEFENDERS, position)]
        mode.advance(arrived + wait + 1, participants)
        cart_delta()
        self.assertAlmostEqual(mode.distance, start + speed)
        self.assertIn((1, escort.PAYLOAD_AUTO_DRIVE_ANIM), client.on())
        self.assertEqual(client.vars[1, escort.PAYLOAD_PUSHERS], (expr.Entity(101),))
        self.assertEqual(client.vars[1, escort.PAYLOAD_BLOCKERS], ())
        self.assertEqual(client.vars[0, escort.PAYLOAD_DIRECTION], 0)
        if scene.hybrid:
            self.assertNotIn(escort.PAYLOAD_GATE_IMPACT_INSTANCE, client.instances)
            rubble, = mode.debris_distances
            debris_stream = statescript.Stream(rubble)
            debris_view = decode.ClientBody()
            intact = mode.door_state(rubble, mode.last_update, None)
            self.assertEqual(intact.pose, escort.DoorPose.CLOSED)
            baseline = mode.door_frame(rubble, intact, None, now=mode.last_update)
            debris_view.apply(debris_stream.full_frame(baseline))

        mode.advance(arrived + wait + (endpoint - start) / speed + 1, ())
        self.assertEqual(mode.distance, endpoint)
        self.assertEqual(mode.movement().position, mode._point(endpoint))
        if scene.hybrid:
            now = mode.last_update
            falling = mode.door_state(rubble, now, intact)
            debris_stream.delivered = debris_stream.last
            frame = mode.door_frame(rubble, falling, intact, now=now)
            (record,) = debris_stream.reliable_frames(frame, 4096, full=False)
            decoded = debris_view.apply(record)
            self.assertEqual(debris_view.graphs[1], escort.GATE_DEBRIS_PROP_GRAPH)
            self.assertEqual(decoded.instances[1].states[escort.GATE_DEBRIS_ANIM],
                             (True, {"counter": 1, "time": 0}))
            self.assertIn((1, escort.GATE_DEBRIS_ALIAS), debris_view.on())
            for elapsed, sample in ((1.5, 1500), (4.0, 3000)):
                now = falling.starts_at + elapsed
                state = mode.door_state(rubble, now, None)
                late_view = decode.ClientBody()
                frame = late_view.apply(statescript.Stream(rubble).full_frame(
                    mode.door_frame(rubble, state, None, now=now),
                ))
                self.assertEqual(frame.instances[1].states[escort.GATE_DEBRIS_ANIM],
                                 (True, {"counter": 1, "time": sample}))
                self.assertIn((1, escort.GATE_DEBRIS_ALIAS), late_view.on())
            debris_stream.delivered = debris_stream.last
            settled_frame = mode.door_frame(rubble, state, falling, now=now)
            (record,) = debris_stream.reliable_frames(settled_frame, 4096, full=False)
            self.assertEqual(debris_view.apply(record).instances[1].states[escort.GATE_DEBRIS_ANIM],
                             (True, {"counter": 2, "time": 3000}))
        mode.advance(mode.last_update + 1, ((101, escort.ATTACKERS, mode.movement().position),))
        self.assertGreater(mode.distance, endpoint)
        mode.advance(mode.last_update + 20, ())
        self.assertEqual(mode.distance, endpoint)

    def test_gibraltar_rider_moves_the_cart_and_opens_the_first_route_gate(self):
        scene = content.game_map(GIBRALTAR, content.ESCORT_MODE)
        gates = tuple(escort.MAP_ENTITY | index for index in (1289, 1469))
        if scene is None or scene.escort is None or not collision.cache_path(GIBRALTAR).is_file():
            self.skipTest("Gibraltar and its collision cache are required")
        mode_gates = {escort.MAP_ENTITY | p.index for p in scene.escort.room_placeables if p.door}
        self.assertTrue(set(gates) <= mode_gates)
        run = ProtocolHarness()
        self.addCleanup(run.close)
        geometry = collision._read_cache(GIBRALTAR, scene.name, False, frozenset())
        run.server.collision_provider_factory = lambda: collision.StaticCollisionProvider(geometry)
        (attack,) = run.server.create_match(
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Attacker", content.SOLDIER, 1, False)],
        )
        link, player = run.connect(attack)
        match = player.match
        mode = match.escort
        catalog = content._map_entries()[GIBRALTAR]
        loading_placeables = catalog["placeables_by_mode"][f"0x{content.ESCORT_MODE:016X}"]
        required = {escort.MAP_ENTITY | index for index in loading_placeables}
        run.until(lambda: required <= {
            record.entity for _, record in link.records if record.op == world.OP_CREATE
        })
        spawn_candidates = decode.ClientBody()

        def read_spawn_candidates(stage):
            run.until(lambda: player.escort_mode_state is not None and player.escort_mode_state.stage == stage
                      and not player.mode_script.data_in_flight)
            for _, record in link.records:
                if record.entity == world.GAME_MODE_ENTITY and record.chunk is not None:
                    spawn_candidates.apply(record)
            return spawn_candidates.vars[0, escort.ACTIVE_SPAWNS]

        initial_spawns = read_spawn_candidates(0)
        self.assertTrue(all(len(team) == 6 for team in initial_spawns))
        spawn_doors = {entity for entity, placement in mode.doors.items()
                       if (0, escort.ATTACKERS) in placement.rooms}
        self.assertTrue(spawn_doors)
        run.until(lambda: all(entity in player.escort_door_states
                              and escort.DOOR_CLOSE_ALIAS in player.escort_door_states[entity].states
                              for entity in (*gates, *spawn_doors)))
        for entity in (*gates, *spawn_doors):
            records = [record for _, record in link.records if record.entity == entity]
            self.assertEqual(records[0].op, world.OP_CREATE)
            baseline = next(record for record in records if record.chunk is not None)
            self.assertIsNotNone(baseline.movement)
            self.assertLess(math.dist(baseline.movement.position, mode.doors[entity].position), 0.002)
            yaw = baseline.movement.yaw / world.ANGLE_UNITS
            pitch = -baseline.movement.body_pitch / world.ANGLE_UNITS
            forward = (math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
            authored = contacts.rotate(mode.doors[entity].rotation, (0.0, 0.0, 1.0))
            self.assertLess(math.dist(forward, authored), 0.001)
            self.assertEqual(baseline.movement.velocity, (0, 0, 0))
        run.message(link, 21616, pick(player.entity, content.SOLDIER))
        run.until(lambda: player.has_body and player.body_script is not None)
        self.assertTrue(all(player.escort_door_states[entity].pose == escort.DoorPose.CLOSED
                            for entity in (*gates, *spawn_doors)))
        run.now = match.assemble_ends
        run.send(link)
        run.step(5)
        run.now = mode.deadline
        run.send(link)
        run.step(5)

        x, y, z = mode.movement().position
        support = match.collision_world.ground(x, z, y + 4, y + 0.5, collision.TEAM1)
        self.assertIsNotNone(support)
        player.mover.position = [x, support.y, z]
        player.mover.body = None
        start = mode.payload_collision.rider(player.mover)
        self.assertIsNotNone(start)
        first_frame = player.body_script.frame + 1
        for frame in range(first_frame, first_frame + 80):
            player.take_commands([Command(frame)])
            run.step()
        self.assertGreater(mode.distance, 1.5)
        self.assertLess(math.dist(mode.payload_collision.rider(player.mover), start), 0.12)
        self.assertTrue(any(record.entity == mode.payload and record.movement is not None
                            and record.movement.body_pitch != 0 for _, record in link.records))
        self.assertEqual(player.escort_door_states[gates[0]].pose, escort.DoorPose.CLOSED)

        for _ in range(100):
            if mode.stage:
                break
            player.mover.position = mode.movement().position
            run.now += 0.984
            run.step()
        self.assertEqual(mode.stage, 1)
        new_spawns = read_spawn_candidates(1)
        for team, old_team in zip(new_spawns, initial_spawns, strict=True):
            self.assertEqual(len(team), 6)
            self.assertTrue(set(team).isdisjoint(old_team))
            self.assertTrue({item.id for item in team} <= required)
        self.assertFalse(any(record.op == world.OP_DESTROY and record.entity in required
                             for _, record in link.records))
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.OPENING
                  and escort.DOOR_OPEN_ANIM in player.escort_door_states[gates[0]].states
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        door = decode.ClientBody()
        poses = []
        for _, record in link.records:
            if record.entity == gates[0] and record.chunk is not None:
                door.apply(record)
                poses.append(door.on())
        self.assertEqual(door.on(), {(1, 0)})
        self.assertIs(door.vars[1, 0x02BB], True)
        self.assertIs(door.vars[0, 0x22A5], True)
        self.assertIs(door.vars[0, 0x475B], False)
        self.assertEqual((door.vars[1, 0x07F0], door.vars[1, 0x07F3]), (7, 10))
        self.assertEqual(door.vars[1, 0x09CB], 0.5)
        self.assertIs(type(door.vars[1, 0x1B7F]), int)
        self.assertEqual(door.vars[1, 0x1B7F], 1)
        self.assertEqual(player.escort_door_states[gates[1]].pose, escort.DoorPose.CLOSED)
        self.assertTrue(set(mode.room_collision[gates[0]]) <= set(mode.payload_collision.room_shapes))
        self.assertEqual(poses[:3], [set(), {(1, 1)}, {(1, 1), (1, 8)}])
        self.assertEqual(poses[-1], {(1, 0)})
        for entity in spawn_doors:
            spawn = decode.ClientBody()
            for _, record in link.records:
                if record.entity == entity and record.chunk is not None:
                    spawn.apply(record)
            self.assertEqual(spawn.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
        link.records.clear()
        run.now = player.escort_door_states[gates[0]].ends_at
        run.send(link)
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.OPEN
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        settle = next(record for _, record in link.records
                      if record.entity == gates[0] and record.chunk is not None)
        finished = door.apply(settle)
        self.assertEqual(door.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
        self.assertEqual(finished.instances[1].states[escort.DOOR_OPEN_ANIM],
                         (True, {"counter": 2, "time": 7000}))
        self.assertIs(door.vars[1, 0x02BB], False)
        self.assertIs(door.vars[0, 0x475B], True)
        self.assertTrue(set(mode.room_collision[gates[0]]).isdisjoint(mode.payload_collision.room_shapes))

        link.records.clear()
        for _ in range(20):
            if not mode.door_open(gates[0]):
                break
            player.mover.position = mode.movement().position
            run.now += 0.984
            run.step()
        self.assertFalse(mode.door_open(gates[0]))
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.CLOSING
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        for _, record in link.records:
            if record.entity == gates[0] and record.chunk is not None:
                frame = door.apply(record)
        self.assertEqual(door.on(), {(1, escort.DOOR_CLOSE_ANIM)})
        self.assertLess(frame.instances[1].states[escort.DOOR_CLOSE_ANIM][1]["time"], 1000)
        run.now = player.escort_door_states[gates[0]].ends_at
        run.send(link)
        run.until(lambda: player.escort_door_states[gates[0]].pose == escort.DoorPose.CLOSED
                  and not player.escort_door_scripts[gates[0]].data_in_flight)
        self.assertTrue(set(mode.room_collision[gates[0]]) <= set(mode.payload_collision.room_shapes))

    def test_waiting_hud_is_removed_when_escort_begins_assembly(self):
        scene = content.game_map(GIBRALTAR, content.ESCORT_MODE)
        if scene is None or scene.escort is None:
            self.skipTest("Gibraltar rail is required in the local 104319 runtime")
        mode = escort.EscortRound(scene.escort)
        stream = statescript.Stream(world.GAME_MODE_ENTITY)
        client = decode.ClientBody()

        waiting = mode.mode_state(0, None)
        client.apply(stream.full_frame(waiting.frame(None)))
        self.assertEqual(client.vars[(1, 59)], escort.Phase.WAITING)
        self.assertNotIn((0, escort.COUNTDOWN), client.vars)
        self.assertIs(client.vars[0, combat.WAITING_FOR_PLAYERS], True)
        self.assertIs(client.vars[3, combat.WAITING_FOR_PLAYERS], True)
        self.assertIn((3, 16), client.on())
        self.assertIn((1, combat.WAITING_FOR_PLAYERS_STATE), client.on())
        self.assertNotIn((1, combat.ASSEMBLE_HEROES_STATE), client.on())
        stream.delivered = stream.last

        assembling = mode.mode_state(5, 35)
        (delta,) = stream.reliable_frames(assembling.frame(waiting), 8192, full=False)
        client.apply(delta)
        self.assertEqual(client.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertEqual(client.vars[(0, escort.COUNTDOWN)], 30)
        self.assertNotIn(3, client.graphs)
        self.assertIs(client.vars[0, combat.WAITING_FOR_PLAYERS], False)
        self.assertIn((1, combat.ASSEMBLE_HEROES_STATE), client.on())
        self.assertNotIn((1, combat.WAITING_FOR_PLAYERS_STATE), client.on())

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
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Attacker", content.SOLDIER, 1, False), (2, "Late", content.SOLDIER, 0, False)],
        )
        link, player = run.connect(attack)
        match = player.match
        mode = match.escort
        mode_ui, cart_ui = decode.ClientBody(), decode.ClientBody()
        self.assertIsNotNone(mode)
        checkpoint_uis = {entity: decode.ClientBody() for _, entity in mode.checkpoints}
        door_uis, visible_barriers, mode_actions, progress_frames = {}, {}, {}, {}
        local_hud_graphs = {0x1FEB, combat.MODE_UI, 0x106B}

        def receive(peer, mode_client, cart_client):
            pos = None
            visible = visible_barriers.setdefault(peer, set())
            for _, record in peer.records:
                if record.entity in mode.barriers or record.entity in mode.overlays:
                    if record.op == world.OP_CREATE:
                        visible.add(record.entity)
                    elif record.op == world.OP_DESTROY:
                        visible.discard(record.entity)
                    if record.chunk is not None:
                        receiver = door_uis.setdefault((peer, record.entity), decode.ClientBody())
                        receiver.apply(record)
                    continue
                if record.entity in mode.doors:
                    receiver = door_uis.setdefault((peer, record.entity), decode.ClientBody())
                elif record.entity == mode.payload:
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
                        if receiver is cart_client and not receiver.applied:
                            self.assertEqual(mode_client.graphs.get(1), escort.MODE_GRAPH)
                        frame = receiver.apply(record)
                        if receiver is mode_client:
                            self.assertTrue(local_hud_graphs.isdisjoint(mode_client.graphs.values()))
                            phase_update = frame.instances.get(1)
                            if (first and phase_update
                                    and phase_update.states.get(escort.MATCH_LIVE) == (True, {})):
                                self.assertIs(cart_client.vars[0, escort.PAYLOAD_ACTIVE], True)
                                self.assertIs(cart_client.vars[1, escort.PAYLOAD_ENABLED], False)
                            mode_actions.setdefault(peer, []).extend(frame.actions)
                            if frame.owner:
                                progress_frames.setdefault(peer, []).append(frame)
            peer.records.clear()
            return pos

        def check_rooms(peer, player):
            self.assertEqual(visible_barriers[peer], mode.room_visuals(player.team))
            for entity in visible_barriers[peer]:
                index = escort.BARRIER_GRAPH if entity in mode.barriers else escort.ROOM_OVERLAY_GRAPH
                if graph.graph(index) is not None:
                    visual = door_uis[(peer, entity)]
                    self.assertEqual(visual.graphs[1], index)
                    self.assertIn((1, 3), visual.on())
            for entity in mode.doors:
                ui = door_uis[(peer, entity)]
                if graph.graph(escort.DOOR_GRAPH) is not None:
                    pose = player.escort_door_states[entity].pose
                    opened = pose != escort.DoorPose.CLOSED
                    self.assertIs(ui.vars[(0, 0x22A5)], opened)
                    self.assertIs(ui.vars[(0, 0x475B)], pose == escort.DoorPose.OPEN)
                    self.assertIs(ui.vars[(0, 0x0804)], True)
                    expected = {(1, state) for state in player.escort_door_states[entity].states}
                    self.assertEqual(ui.on(), expected)
                    playing = pose == escort.DoorPose.OPENING and (1, escort.DOOR_OPEN_ANIM) in expected
                    self.assertIs(ui.vars[1, 0x02BB], playing)
                    self.assertIs(ui.vars[1, 0x3DF2], not playing and pose != escort.DoorPose.OPEN)

        def check_cart_route(cart):
            # 1225 state 119 positions the 2C8 HUD markers using these replicated values.
            route_length = cart.vars[0, 0x1D9F]
            self.assertAlmostEqual(route_length, mode.length, delta=0.001)
            self.assertEqual(cart.vars[0, 0x1EC7], 1.0)
            self.assertEqual(cart.vars[1, escort.PAYLOAD_CHECKPOINT_OFFSET], 0)
            fractions = cart.vars[1, 0x1D9E]
            for fraction, (distance, _) in zip(fractions, mode.checkpoints, strict=True):
                self.assertAlmostEqual(fraction * route_length, distance, delta=0.001)
            stage = cart.vars[1, 0x2934]
            start = fractions[stage - 1] * route_length if stage else 0.0
            section_length = fractions[stage] * route_length - start
            distance = start + cart.vars[0, 0x009A] * section_length
            self.assertAlmostEqual(cart.vars[0, 0x01DF], distance, delta=0.001 + 0.0005 * section_length)

        run.until(lambda: player.mode_script.delivered and player.escort_payload_script.delivered
                  and not player.mode_script.data_in_flight and player.escort_mode_state is not None
                  and combat.ASSEMBLE_HEROES_STATE in player.escort_mode_state.states)
        run.until(lambda: {escort.MAP_ENTITY | index for index in scene.placeables} <= {
            record.entity for _, record in link.records if record.op == world.OP_CREATE
        })
        created = {record.entity for _, record in link.records if record.op == world.OP_CREATE}
        self.assertIn(mode.payload, created)
        self.assertTrue({entity for _, entity in mode.checkpoints} <= created)
        pos = receive(link, mode_ui, cart_ui)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ASSEMBLE)
        self.assertIn((1, combat.ASSEMBLE_HEROES_STATE), mode_ui.on())
        self.assertNotIn((1, combat.WAITING_FOR_PLAYERS_STATE), mode_ui.on())
        self.assertIs(mode_ui.vars[0, combat.WAITING_FOR_PLAYERS], False)
        self.assertIn((1, 95), mode_ui.on())
        self.assertEqual(mode_ui.vars[(0, 1923)], expr.Asset(0x0DE0000000006115))
        for i, checkpoint in enumerate(checkpoint_uis.values()):
            self.assertEqual(checkpoint.vars[(0, escort.OBJECTIVE_KIND)], escort.PAYLOAD_OBJECTIVE)
            self.assertEqual(checkpoint.vars[(0, 10548)], i)
            self.assertEqual(checkpoint.vars[(0, 2190)], expr.Entity(mode.payload))
        self.assertEqual(cart_ui.on(), {(1, 23)})
        check_cart_route(cart_ui)
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
        self.assertNotIn((1, combat.ASSEMBLE_HEROES_STATE), mode_ui.on())
        self.assertIs(cart_ui.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], False)
        self.assertFalse(player.select_open)
        check_rooms(link, player)

        late_link, late_player = run.connect(late)
        run.until(lambda: late_player.escort_payload_script.delivered)
        late_mode, late_cart = decode.ClientBody(), decode.ClientBody()
        receive(late_link, late_mode, late_cart)
        self.assertEqual(late_mode.vars[(1, 59)], escort.Phase.SETUP)
        self.assertIs(late_cart.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertTrue(late_player.select_open)
        check_rooms(late_link, late_player)
        late_first_spawn = late_player.spawn

        run.now = mode.deadline
        run.send(link)
        run.send(late_link)
        run.until(lambda: all(p.escort_hud_bound and not p.mode_script.data_in_flight
                              for p in (player, late_player)))
        receive(link, mode_ui, cart_ui)
        receive(late_link, late_mode, late_cart)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.ACTIVE)
        self.assertEqual(mode_ui.vars[(0, escort.COUNTDOWN)], 240)
        self.assertEqual(mode_ui.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.ATTACKERS]))
        self.assertEqual(late_mode.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.DEFENDERS]))
        self.assertNotIn((1, 14), mode_ui.on())
        self.assertIn((1, escort.MATCH_LIVE), mode_ui.on())
        self.assertIn((1, escort.ESCORT_HUD), mode_ui.on())
        self.assertIn((1, escort.HUD_PROGRESS), mode_ui.on())
        self.assertIn((1, escort.HUD_PAYLOAD), mode_ui.on())
        self.assertIn((1, escort.HUD_OBJECTIVE_KIND), mode_ui.on())
        self.assertIn((2, 2), mode_ui.on())
        self.assertIn((2, 54), mode_ui.on())
        self.assertNotIn((2, 10), mode_ui.on())
        self.assertEqual(mode_ui.records[2], (escort.MODE_MESSAGES, (1, 12)))
        self.assertEqual(mode_ui.records[5], (escort.MODE_HUD_VALUES, (2, 50)))
        self.assertIn((5, 0), mode_ui.on())
        context = mode_ui.vars[0, escort.HUD_ROUND_DATA]
        self.assertEqual(context.get(escort.CHECKPOINT_INDEX), 0)
        self.assertEqual(context.get(escort.HUD_CLOCK), 240)
        self.assertIs(context.get(0x2259), True)  # the native presenter selects the payload title branch
        self.assertIs(mode_ui.vars[0, 0x4C47], True)
        self.assertEqual(mode_ui.vars[0, 59], escort.Phase.ACTIVE)
        self.assertEqual(mode_ui.vars[0, 2190], expr.Entity(mode.payload))
        self.assertEqual(mode_ui.vars[0, 12376], tuple(expr.Entity(entity) for _, entity in mode.checkpoints))
        self.assertEqual([(target, node) for _, target, node in mode_actions[link]],
                         [(1, escort.HUD_CART_CHANGED)])
        self.assertEqual([(target, node) for _, target, node in mode_actions[late_link]],
                         [(1, escort.HUD_CART_CHANGED)])
        for peer, client in ((link, mode_ui), (late_link, late_mode)):
            (binding_frame,) = progress_frames[peer]
            self.assertGreater(binding_frame.cmfd, 0)
            self.assertTrue(all(binding_frame.instances[1].states[state] == (True, {})
                                for state in escort.HUD_PROGRESS_BINDINGS))
            self.assertTrue({(1, state) for state in escort.HUD_PROGRESS_BINDINGS} <= client.on())
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], False)
        self.assertEqual(cart_ui.vars[1, 0x2934], 0)
        self.assertEqual(cart_ui.vars[1, 0x1A9F], ())
        self.assertEqual(cart_ui.vars[1, 0x1AA0], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 0)
        check_cart_route(cart_ui)
        self.assertEqual(cart_ui.vars[0, 0x01DF], 0.0)
        native_adder = graph.graph(escort.MODE_GRAPH).state(11)
        self.assertTrue(native_adder.client_only)
        self.assertEqual(native_adder.field("m_36F7B64C.m_36F7B64C.m_C974E369.m_graph"),
                         "0x0580000000001FEB")
        container = graph.graph(0x1FEB).state(2)
        self.assertEqual(container.cls, "STUStatescriptStateUXPresenter")
        self.assertEqual(container.field("m_45216F79.m_D44EB181.m_identifier"), 0x2CB5)
        self.assertEqual(container.field("m_45017CEA.m_identifier"), 0x2F9E)

        class ObjectiveContext(expr.Context):
            def var(self, scope, variable):
                return mode_ui.vars.get((0 if scope else 1, variable))

            def member(self, entity, variable):
                if entity == mode.payload:
                    return cart_ui.vars.get((0, variable))
                return checkpoint_uis[entity].vars.get((0, variable))

        hud = graph.graph(escort.MODE_GRAPH).state(escort.ESCORT_HUD)
        objective = next(param["m_value"] for param in hud.field("m_params")
                         if param["m_B5051BCE"] == "0x0A400000000004E2")
        self.assertEqual(expr.evaluate(objective, ObjectiveContext()), expr.Asset(0x0D8000000000088E))
        native_binding = graph.graph(escort.MODE_GRAPH).state(95)
        cart = next(row["m_value"] for row in native_binding.field("m_values")
                    if row["m_identifier"] == "0x0D80000000003267")
        self.assertEqual(expr.evaluate(cart, ObjectiveContext()), expr.Entity(mode.payload))
        check_rooms(link, player)
        check_rooms(late_link, late_player)

        with patch.object(run.server.environment, "wall_time", return_value=run.now):
            (active_join,) = run.server.join_match(match, [(3, "Active join", content.SOLDIER, 0, False)])
        active_link, active_player = run.connect(active_join)
        run.until(lambda: active_player.escort_hud_bound and not active_player.mode_script.data_in_flight)
        active_mode, active_cart = decode.ClientBody(), decode.ClientBody()
        receive(active_link, active_mode, active_cart)
        self.assertEqual(active_mode.vars[1, 59], escort.Phase.ACTIVE)
        self.assertEqual(active_mode.vars[1, escort.HUD_CART_REFERENCE], expr.Entity(mode.payload))
        self.assertIn((5, 0), active_mode.on())
        self.assertEqual(active_mode.vars[0, 1923], expr.Asset(escort.ACTIVE_HEADING[escort.DEFENDERS]))
        self.assertEqual(len(progress_frames[active_link]), 1)
        self.assertTrue({(1, state) for state in escort.HUD_PROGRESS_BINDINGS} <= active_mode.on())
        self.assertIs(active_cart.vars[1, escort.PAYLOAD_ENABLED], False)
        self.assertIs(active_cart.vars[0, escort.PAYLOAD_ACTIVE], True)
        check_cart_route(active_cart)
        self.assertEqual([(target, node) for _, target, node in mode_actions[active_link]],
                         [(1, escort.HUD_CART_CHANGED)])
        for entity in mode.doors:
            if mode.door_open(entity):
                door = door_uis[active_link, entity]
                self.assertEqual(door.on(), {(1, escort.DOOR_OPEN_ANIM), (1, escort.DOOR_OPEN_ALIAS)})
                self.assertIs(door.vars[1, 0x3DF1], True)
                self.assertIs(door.vars[1, 0x02BB], False)

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
            check_cart_route(cart_ui)
            self.assertGreater(cart_ui.vars[0, 0x01DF], 0.0)
            self.assertLess(cart_ui.vars[0, 0x01DF], cart_ui.vars[0, 0x1D9F])
            self.assertEqual(cart_ui.vars[1, 0x1A9F], (expr.Entity(player.body),))
            self.assertEqual(cart_ui.vars[0, 0x01B3], 1)
            self.assertGreater(mode_ui.vars[(0, 13468)], 0.0)
            native_progress = graph.graph(escort.MODE_GRAPH).state(escort.HUD_PROGRESS).field("m_value")
            progress = expr.evaluate(native_progress, ObjectiveContext())
            self.assertGreater(progress, 0.0)
            self.assertEqual(progress, cart_ui.vars[(0, 0x009A)])
            self.assertAlmostEqual(progress, mode_ui.vars[0, 13468], delta=0.002)
            for state, key in zip(escort.HUD_PROGRESS_BINDINGS, (0x349C, 0x34F3), strict=True):
                (field,) = graph.graph(escort.MODE_GRAPH).state(state).field("m_values")
                self.assertEqual(int(field["m_identifier"], 16) & 0xFFFF, key)
                self.assertIn((1, state), mode_ui.on())
                self.assertGreater(expr.evaluate(field["m_value"], ObjectiveContext()), 0.0)

        run.message(late_link, 21616, pick(late_player.entity, content.SOLDIER))
        run.until(lambda: late_player.has_body)
        player.mover.position = late_player.mover.position = mode.movement().position
        run.step(12)
        for peer, mode_ui_peer, cart in ((link, mode_ui, cart_ui), (late_link, late_mode, late_cart)):
            receive(peer, mode_ui_peer, cart)
            self.assertEqual(cart.vars[1, 0x1A9F], (expr.Entity(player.body),))
            self.assertEqual(cart.vars[1, 0x1AA0], (expr.Entity(late_player.body),))
            self.assertEqual(cart.vars[0, 0x01B3], 0)
        late_player.mover.position = late_first_spawn[0]
        run.step(12)
        receive(link, mode_ui, cart_ui)
        self.assertEqual(cart_ui.vars[1, 0x1AA0], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 1)
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
            receive(late_link, late_mode, late_cart)
            check_rooms(link, player)
            check_rooms(late_link, late_player)
            check_cart_route(cart_ui)
            check_cart_route(late_cart)
            self.assertLess(math.dist(mode.movement().position, pos), 0.002)
            windows.append(mode.deadline - end_before)
            if stage < 3:
                self.assertEqual(mode_ui.vars[0, escort.CHECKPOINT_INDEX], stage)
                for ui, cart in ((mode_ui, cart_ui), (late_mode, late_cart)):
                    offset = cart.vars[1, escort.PAYLOAD_CHECKPOINT_OFFSET]
                    captured = ui.vars[0, escort.CHECKPOINT_INDEX] - offset
                    self.assertEqual(captured, stage)
                self.assertEqual(mode_ui.vars[0, escort.HUD_ROUND_DATA].get(escort.CHECKPOINT_INDEX), stage)
                self.assertEqual(mode_ui.vars[(1, 10551)], expr.Entity(mode.checkpoints[stage][1]))
                self.assertEqual(cart_ui.on(), {(1, escort.PAYLOAD_WAYPOINT)})
                for waiting_cart in (cart_ui, late_cart):
                    self.assertEqual(waiting_cart.vars[1, escort.PAYLOAD_PUSHERS], ())
                    self.assertEqual(waiting_cart.vars[0, escort.PAYLOAD_DIRECTION], 0)
                self.assertAlmostEqual(mode.distance, mode.checkpoints[stage - 1][0])
                self.assertNotEqual(player.spawn, first_spawn)
                self.assertNotEqual(late_player.spawn, late_first_spawn)
        self.assertEqual(windows, [150.0, 90.0, 0.0])
        self.assertEqual(mode.winner, 1)
        self.assertEqual(mode_ui.vars[(1, 59)], escort.Phase.COMPLETE)
        self.assertEqual(mode_ui.vars[(1, 7525)], escort.ATTACKERS)
        self.assertIn((1, 20), mode_ui.on())  # 0F4A sends the winning team in message 0695
        self.assertEqual(cart_ui.on(), {(1, escort.PAYLOAD_RESULT_ANIM)})
        self.assertIs(cart_ui.vars[1, escort.PAYLOAD_ENABLED], True)
        self.assertIs(cart_ui.vars[0, escort.PAYLOAD_ACTIVE], False)
        self.assertEqual(cart_ui.vars[1, 0x1A9F], ())
        self.assertEqual(cart_ui.vars[0, 0x01B3], 0)
        self.assertAlmostEqual(cart_ui.vars[0, 0x01DF], cart_ui.vars[0, 0x1D9F], delta=0.001)
