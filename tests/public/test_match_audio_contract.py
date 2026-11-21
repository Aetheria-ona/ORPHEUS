import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from ow174.game import content, escort, match_audio
from tests.public.test_escort_objective_placeables_contract import FIXTURES


class MatchAudioContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((FIXTURES / "match-audio-v1.json").read_text(encoding="utf-8"))
        cls.inputs, cls.expected = fixture["input"], fixture["expected"]

    @staticmethod
    def lines(previous, current, mode, team, single_countdown=False):
        cues = match_audio.speech(previous, current, mode, team, single_countdown=single_countdown)
        return [f"{line:04X}" for _, line in cues]

    def test_escort_both_teams_and_late_join(self):
        timeline = [match_audio.Timeline(**{**row, "phase": escort.Phase[row["phase"]]})
                    for row in self.inputs["escort"]]
        for team, want in ((escort.DEFENDERS, "escort_defense"), (escort.ATTACKERS, "escort_attack")):
            trace = [self.lines(old, new, content.ESCORT_MODE, team)
                     for old, new in zip([None, *timeline[:-1]], timeline, strict=True)]
            self.assertEqual(trace, self.expected[want])
            overtime = next(frame for frame in timeline if frame.overtime)
            self.assertEqual(self.lines(None, overtime, content.ESCORT_MODE, team), [])
            near_finish = next(frame for frame in timeline if frame.near_finish)
            self.assertEqual(self.lines(None, near_finish, content.ESCORT_MODE, team), [])
            self.assertEqual(self.lines(None, timeline[-1], content.ESCORT_MODE, team), [])

    def test_attack_defend_objective_and_existing_shared_start(self):
        setup = match_audio.Timeline(escort.Phase.SETUP, 1)
        active = match_audio.Timeline(escort.Phase.ACTIVE, 230)
        second = replace(active, stage=1)
        self.assertEqual(self.lines(setup, active, content.ASSAULT_MODE, escort.ATTACKERS),
                         self.expected["assault_attack_a"])
        self.assertEqual(self.lines(active, second, content.ASSAULT_MODE, escort.DEFENDERS),
                         self.expected["assault_defense_b"])
        assembly = match_audio.Timeline(escort.Phase.ASSEMBLE, 1)
        self.assertEqual(self.lines(assembly, active, 0x0230000000000016, escort.ATTACKERS, True),
                         self.expected["hybrid_attack_start"])
        self.assertEqual(self.lines(assembly, active, match_audio.CONTROL_MODE, escort.DEFENDERS, True),
                         self.expected["control_start"])
        spectator = content.SPECTATOR_TEAM
        self.assertEqual(self.lines(setup, active, content.ASSAULT_MODE, spectator), [])

    def test_native_mode_action_is_current_and_deduplicated(self):
        def snapshot(phase, seconds, stage, overtime):
            states = frozenset(escort.round_phase_states(phase, True, seconds, overtime))
            return escort.ModeState({escort.COUNTDOWN: (seconds, ()), escort.CHECKPOINT_INDEX: (stage, ())},
                                    {59: (phase, ())}, states)

        for row, action in zip(self.inputs["mode_deltas"], self.expected["mode_actions"], strict=True):
            phase, before, after, old_stage, stage, old_overtime, overtime = row
            phase = escort.Phase[phase]
            with self.subTest(row=row):
                self.assertEqual(match_audio.mode_action(snapshot(phase, before, old_stage, old_overtime),
                                                         snapshot(phase, after, stage, overtime)), action)

    def test_assembly_starts_immediately_and_paces_hero_prompt_from_delivery(self):
        self.assert_playback("assembly_playback", match_audio.Timeline(escort.Phase.ASSEMBLE, 24))

    def test_finish_music_window_uses_remaining_route_distance(self):
        payload = escort.PayloadState(None, 2, 0.0, 0.0, 100.0, (), True, True, 0, (), ())
        near = [replace(payload, active=active, distance=payload.route_length - remaining).near_finish
                for active, remaining in self.inputs["finish_distances"]]
        self.assertEqual(near, self.expected["finish_near"])
        self.assertEqual([f"{effect & 0xFFFF:04X}" for effect in match_audio.CHECKPOINT_EFFECTS],
                         self.expected["checkpoint_effects"])

    def test_capture_to_objective_b_waits_for_the_full_first_line_after_delivery(self):
        active = match_audio.Timeline(escort.Phase.ACTIVE, 90)
        self.assert_playback("capture_playback", replace(active, stage=1), previous=active)

    def assert_playback(self, name, timeline, *, previous=None):
        client = Mock()
        client.queue_combat_notice.return_value = 7
        viewer = SimpleNamespace(
            match=SimpleNamespace(game_map=SimpleNamespace(mode_guid=content.ASSAULT_MODE)),
            team=escort.ATTACKERS, client=client, has_body=False,
        )
        playback = match_audio.Playback(previous=previous)
        trace = []
        for now, delivered in self.inputs[name]:
            client.reliable_delivered.return_value = delivered
            playback.update(viewer, now, timeline, single_countdown=False)
            trace.append([f"{call.args[2]['+0x90'] & 0xFFFF:04X}"
                          for call in client.queue_combat_notice.call_args_list])
            client.queue_combat_notice.reset_mock()
        self.assertEqual(trace, self.expected[name])
