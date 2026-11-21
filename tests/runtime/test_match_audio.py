"""Check Athena notices and native music triggers on the encrypted match link."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from ow174.game import content, escort, match_audio, messages, world
from ow174.game.bits import BitReader
from ow174.game.link import KIND_FRAME, SYN
from ow174.game.script import decode, expr, graph
from tests.runtime.test_assault_round import PARIS
from tests.runtime.test_escort_round import RIALTO
from tests.runtime.test_game_repeated_lifecycles import ProtocolHarness
from tests.runtime.test_game_server import pick


def voices_in(packet):
    if packet.kind != KIND_FRAME:
        return []
    reader = BitReader(packet.payload)
    reader.var_a()
    return [(ordinal, value) for ordinal, msg_id, value in messages.read_channel(reader)[0]
            if msg_id == 27500]


def voice_packets(link):
    notices = dict(pair for packet in link.got for pair in voices_in(packet))
    return [notices[key] for key in sorted(notices)]


class MatchAudioWireTests(unittest.TestCase):
    def test_escort_audio_from_assembly_to_overtime(self):
        self._check_round(RIALTO, content.ESCORT_MODE)

    def test_assault_audio_from_assembly_to_overtime(self):
        self._check_round(PARIS, content.ASSAULT_MODE)

    def _check_round(self, map_guid, mode_guid):
        scene = content.game_map(map_guid, mode_guid)
        is_payload = mode_guid == content.ESCORT_MODE
        if (scene is None or (scene.escort if is_payload else scene.assault) is None
                or not all(graph.graph(index) for index in (0x0EC2, match_audio.CAPTURE_SOUND_GRAPH))):
            self.skipTest("Requires the local 104319 map and graph data")
        run = ProtocolHarness()
        self.addCleanup(run.close)
        attacking, defending, late = run.server.create_match(
            replace(scene, custom_start=content.START_IMMEDIATELY),
            [(1, "Attack", content.SOLDIER, escort.ATTACKERS, False),
             (2, "Defense", content.SOLDIER, escort.DEFENDERS, False),
             (3, "Late", content.SOLDIER, escort.DEFENDERS, False)],
        )
        attack_link, attacker = run.connect(attacking)
        defense_link, defender = run.connect(defending)
        late_link = run.link(late)
        run.send(late_link, SYN)
        run.send(late_link)
        run.until(lambda: 20300 in late_link.messages)
        late_player = run.server.clients[late_link.conn].player
        match = attacker.match
        mode = match.objective_round

        def cues(link):
            return [(row["+0x88"] & 0xFFFF, row["+0x90"] & 0xFFFF) for row in voice_packets(link)]

        def jump(to):
            run.now = to
            for link in run.game.links.values():
                run.send(link)
            run.step(16)

        run.until(lambda: all(cues(link) for link in (attack_link, defense_link)))
        self.assertLess(run.now, match.assemble_ends - 27)
        self.assertEqual(cues(attack_link), [match_audio.PREPARE[1]])
        self.assertEqual(cues(defense_link), [match_audio.PREPARE[0]])
        for link in (attack_link, defense_link):
            notice = voice_packets(link)[0]
            self.assertEqual(notice["+0x78"]["+0x0"], world.GAME_MODE_ENTITY)
            self.assertEqual(notice["+0x7C"]["+0x0"], 0xFFFFFFFF)
            self.assertEqual(notice["+0x88"] >> 48, 0x0EE0)
            self.assertEqual(notice["+0x90"] >> 48, 0x0760)
        run.until(lambda: all(p.match_audio.follow_up and p.match_audio.follow_up.due is not None
                              for p in (attacker, defender)))
        jump(max(p.match_audio.follow_up.due for p in (attacker, defender)))
        self.assertEqual(cues(attack_link)[-1], match_audio.SELECT_HERO)
        self.assertEqual(cues(defense_link)[-1], match_audio.SELECT_HERO)
        run.message(attack_link, 21616, pick(attacker.entity, content.SOLDIER))
        run.until(lambda: attacker.has_body)

        jump(match.assemble_ends - 10)
        jump(match.assemble_ends)
        jump(mode.deadline - 30)
        self.assertEqual(cues(attack_link)[-1], match_audio.SETUP_CLOCK[30][1])
        self.assertEqual(cues(defense_link)[-1], match_audio.SETUP_CLOCK[30][0])
        jump(mode.deadline - 20)
        self.assertEqual(cues(attack_link)[-1], match_audio.SETUP_CLOCK[30][1])
        self.assertEqual(cues(defense_link)[-1], match_audio.SETUP_CLOCK[30][0])
        jump(mode.deadline - 15)
        for second in (5, 4, 3, 2, 1):
            jump(mode.deadline - second)
        self.assertEqual(cues(attack_link)[-5:], list(match_audio.NUMBERS.values()))
        self.assertEqual(cues(defense_link)[-5:], list(match_audio.NUMBERS.values()))

        jump(mode.deadline)
        jump(run.now + 2.0)
        objective = match_audio.PAYLOAD if is_payload else match_audio.OBJECTIVE_A
        self.assertEqual(cues(attack_link)[-2:], [match_audio.ATTACK_STARTS[1], objective[1]])
        self.assertEqual(cues(defense_link)[-2:], [match_audio.ATTACK_STARTS[0], objective[0]])

        run.message(late_link, 21601, {})
        run.until(lambda: late_player.mode_script.delivered and late_player.script.delivered)
        run.step(16)
        self.assertEqual(cues(late_link), [])
        if is_payload:
            mode._push(mode.checkpoints[0][0], run.now)
            expected_stage = (match_audio.CHECKPOINT, match_audio.CHECKPOINT)
        else:
            attacker.mover.position = list(mode.points[0].boxes[0][4:7])
            mode.progress = 0.9999
            expected_stage = (match_audio.POINT_A_TAKEN[1], match_audio.POINT_A_TAKEN[0])
        run.step(20)
        self.assertEqual(cues(attack_link)[-1], expected_stage[0])
        self.assertEqual(cues(defense_link)[-1], expected_stage[1])
        self.assertEqual(cues(late_link), [expected_stage[1]])
        jump(run.now + 2.0)
        if not is_payload:
            self.assertEqual(cues(attack_link)[-1], expected_stage[0])
            self.assertEqual(cues(defense_link)[-1], expected_stage[1])
            jump(run.now + 1.5)
        next_objective = match_audio.CHECKPOINT if is_payload else match_audio.OBJECTIVE_B[0]
        self.assertEqual(cues(late_link)[-1], next_objective)

        if is_payload:
            mode._push(mode.checkpoints[1][0] - mode.distance, run.now)
            run.step(20)
            self.assertEqual(cues(late_link).count(match_audio.CHECKPOINT), 2)

            warning_edge = mode.length - escort.FINISH_WARNING_DISTANCE
            mode._push(warning_edge + 0.5 - mode.distance, run.now)
            mode.last_attacker = run.now
            run.step(20)
            for link in (attack_link, defense_link, late_link):
                self.assertEqual(cues(link).count(match_audio.NEAR_FINISH), 1)

            with patch.object(run.server.environment, "wall_time", return_value=run.now):
                (finish_viewer,) = run.server.join_match(
                    match, [(4, "Finish viewer", content.SOLDIER, escort.ATTACKERS, False)],
                )
            finish_link, finish_player = run.connect(finish_viewer)
            run.until(lambda: finish_player.match_audio.previous is not None)
            self.assertTrue(finish_player.match_audio.previous.near_finish)
            self.assertEqual(cues(finish_link), [])
            cart_baseline = next(record for _, record in finish_link.records
                                 if record.entity == mode.payload and record.chunk is not None)
            cart = decode.ClientBody()
            cart.apply(cart_baseline)
            self.assertIn((1, escort.PAYLOAD_FINISH_MUSIC), cart.on())

            previous_distance = mode.distance
            jump(mode.last_attacker + escort.ROLLBACK_DELAY + 3)
            self.assertLess(mode.distance, min(warning_edge, previous_distance))
            mode._push(warning_edge + 1 - mode.distance, run.now)
            mode.last_attacker = run.now
            run.step(20)
            for link in (attack_link, defense_link, late_link):
                self.assertEqual(cues(link).count(match_audio.NEAR_FINISH), 2)
                self.assertNotIn((0x19C, 0x0621), cues(link))

        jump(mode.deadline - 30)
        self.assertEqual(cues(late_link)[-1], match_audio.ROUND_CLOCK[30])
        jump(mode.deadline - 10)
        run.step(64)
        jump(mode.deadline - 1)
        active_point = mode.movement().position if is_payload else mode.points[mode.stage].boxes[0][4:7]
        attacker.mover.position = list(active_point)
        jump(mode.deadline)
        self.assertTrue(mode.overtime)
        for link in (attack_link, defense_link, late_link):
            self.assertEqual(cues(link).count(match_audio.OVERTIME), 1)
        attacker.mover.position = [10_000.0, 0.0, 10_000.0]
        jump(run.now + escort.OVERTIME_SECONDS)
        self.assertEqual(mode.winner, escort.DEFENDERS)
        self.assertEqual(cues(attack_link)[-1], match_audio.RESULT[0])
        self.assertEqual(cues(defense_link)[-1], match_audio.RESULT[1])

        definition = graph.graph(escort.MODE_GRAPH)
        capture_graph = graph.graph(match_audio.CAPTURE_SOUND_GRAPH)
        sound_target = match_audio.CAPTURE_SOUND_INSTANCE
        sound_action = match_audio.CAPTURE_SOUND_ACTION
        self.assertEqual(capture_graph.node(match_audio.CAPTURE_SOUND_ACTION).field("m_effect.m_identifier"),
                         match_audio.CAPTURE_EFFECT_VAR)
        for team, link, capture_cue in zip((escort.ATTACKERS, escort.DEFENDERS),
                                           (attack_link, defense_link), expected_stage, strict=True):
            client = decode.ClientBody()
            cart = decode.ClientBody()
            packets = {packet.seq: packet for packet in link.got}
            action_nodes, states = [], set()
            music_edges = []
            tenths = []
            captured_sound = 0
            for seq, record in link.records:
                if record.chunk is None:
                    continue
                receiver = client if record.entity == world.GAME_MODE_ENTITY else None
                if is_payload and record.entity == mode.payload:
                    receiver = cart
                if receiver is None:
                    continue
                reader = BitReader(record.chunk.getvalue())
                first = decode.w_u32(reader)
                if first + decode.w_var(reader) <= receiver.applied:
                    continue
                frame = receiver.apply(record)
                if receiver is cart:
                    playing = (1, escort.PAYLOAD_FINISH_MUSIC) in cart.on()
                    if not music_edges or music_edges[-1] != playing:
                        music_edges.append(playing)
                    continue
                nodes = [node for _, target, node in frame.actions if target == 1]
                clock = client.vars.get((0, escort.COUNTDOWN), 0)
                if (client.vars.get((1, 59)) == escort.Phase.ACTIVE and 9 <= clock <= 10
                        and (not tenths or clock != tenths[-1])):
                    tenths.append(clock)
                    hud_clock = client.vars[0, escort.HUD_ROUND_DATA].get(escort.HUD_CLOCK)
                    self.assertEqual(hud_clock, clock)
                if any((target, node) == (sound_target, sound_action)
                       for _, target, node in frame.actions):
                    captured_sound += 1
                    effect = 0x0300000000000B45 if team == escort.ATTACKERS else 0x0300000000000D4C
                    self.assertEqual(client.vars[sound_target, match_audio.CAPTURE_EFFECT_VAR],
                                     expr.Asset(effect))
                    lines = {row["+0x90"] & 0xFFFF for _, row in voices_in(packets[seq])}
                    self.assertIn(capture_cue[1], lines)
                action_nodes.extend(nodes)
                states.update(client.on())
            self.assertEqual(captured_sound, 2 if is_payload else 1)
            self.assertEqual(tenths, [expr.f32(i / 10) for i in range(100, 89, -1)])
            self.assertNotIn(match_audio.CAPTURE_SOUND_INSTANCE, client.instances)
            if is_payload:
                self.assertEqual(music_edges, [False, True, False, True, False])
            action_messages = [
                int(definition.node(node).field("m_gameMessage.m_gameMessage"), 16) & 0xFFFF
                for node in action_nodes if node != escort.HUD_CART_CHANGED
            ]
            self.assertEqual(action_messages, [0x0267, 0x0D0A, *([0x0FD3] * 5), 0x0D10, 0x0D0F, 0x0D11])
            self.assertIn((1, escort.FINAL_MUSIC_STATE), states)
            self.assertIn((1, escort.OVERTIME_MUSIC_SCOPE), states)
            self.assertNotIn((1, escort.OVERTIME_MUSIC_SCOPE), client.on())
