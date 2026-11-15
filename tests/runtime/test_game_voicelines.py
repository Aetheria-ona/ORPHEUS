"""Spawn voice lines (ow174/game/voicelines.py): that beginning a hero's networked voice stimulus on a
spawned body reaches the client and stays in step, read back through the body-frame client model of
test_script_driver.py."""

import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ow174.game import voicelines
from ow174.game.content import heroes
from ow174.game.script import decode
from ow174.game.script import graph as graphs
from ow174.game.script.driver import POLLED_SWITCHES, SETTLE_FRAMES, TRUSTED_NATIVES, BodyScript
from tests.runtime.test_game_combat import Run

START = 1000


def server_on(body: BodyScript) -> set:
    """The networked states the server has on, less the uncertain switches it keeps off the client (the
    same measure test_script_driver.py checks the client against)."""
    on = {
        (item.id, state.index)
        for item in body.component.instances.values()
        for state in item.states.values()
        if state.active and state.networked
    }
    uncertain = {
        (item.id, state.index)
        for item in body.component.instances.values()
        for state in item.states.values()
        if state.node.cls in POLLED_SWITCHES and state.natives - TRUSTED_NATIVES
    }
    return on - uncertain


def hero_guid(name: str) -> int:
    return next(int(body["hero"], 16) for body in graphs.bodies().values() if body["name"] == name)


class StubMatch:
    def label(self) -> str:
        return "test match"


class StubPlayer:
    def __init__(self, script: BodyScript) -> None:
        self.body_script = script
        self.name = "Tester"


class PlayerVoiceTests(unittest.TestCase):
    def setUp(self):
        fixture_root = Path(os.environ.get(
            "ORPHEUS_BEHAVIOR_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures" / "behavior",
        ))
        fixture = json.loads((fixture_root / "player-voice-v1.json").read_text(encoding="utf-8"))
        self.input = fixture["input"]
        self.expected = fixture["expected"]
        self.run = Run(names=("Speaker", "Ally", "Enemy"))
        self.speaker, ally, _ = self.run.players
        ally.team = self.speaker.team
        self.run.match.switch_hero(self.speaker, heroes()[int(self.input["hero"], 16)])
        self.speaker.skin = (int(self.input["skin"], 16), False)
        self.advance(40)
        self.enterContext(
            mock.patch.object(voicelines, "player_voice_catalog", return_value=self.input["fixtures"])
        )

    def advance(self, frames):
        for _ in range(frames):
            self.run.step({})

    def assert_audience(self, friendly, hostile):
        for player, cues in zip(self.run.players, (friendly, friendly, hostile), strict=True):
            expected = [{
                "+0x78": {"+0x0": self.speaker.body},
                "+0x7C": {"+0x0": player.body},
                "+0x80": {"+0x0": 0xFFFFFFFF},
                "+0x88": int(stimulus, 16),
                "+0x90": int(line, 16),
                "+0x98": 0,
                "+0xA0": 0,
            } for stimulus, line in cues]
            self.assertEqual(player.client.messages(27500), expected, player.name)

    def test_wheel_actions_use_the_speakers_voice_set_and_team(self):
        for action in self.input["actions"]:
            self.run.match.communication_action(self.speaker, {
                "+0x78": 0x0A90000000000000 | int(action, 16), "+0x80": True,
            })
            self.advance(130)
        self.assert_audience(self.expected["wheel_friendly"], self.expected["wheel_hostile"])

    def test_equipped_wheel_slots_deliver_notices_without_replaying_the_client_effect(self):
        body = self.speaker.body_script
        body.world.voice_effects = tuple(int(effect, 16) for effect in self.input["equipped_effects"])
        for action in self.input["equipped_choices"]:
            self.run.match.communication_action(self.speaker, {
                "+0x78": 0x0A90000000000000 | int(action, 16), "+0x80": True,
            })
            self.advance(130)
            self.assertFalse(any(event.node == 72 for event in body.world.wheel_callouts))
        self.assert_audience(self.expected["equipped_all"], self.expected["equipped_all"])

    def test_need_healing_plays_at_full_and_reduced_health_without_an_aimed_target(self):
        vitals = self.run.match.combat.body_vitals(self.speaker.body, self.speaker)
        action = 0x0A90000000000000 | int(self.input["healing_action"], 16)
        for damage in (0, 70):
            if damage:
                vitals.damage(damage)
            self.run.match.communication_action(self.speaker, {"+0x78": action, "+0x80": False})
            self.advance(130)
            self.assert_audience(self.expected["healing_friendly"], [])
            for player in self.run.players:
                player.client.reliable.clear()

    def test_aimed_healing_plays_one_notice_for_each_authored_branch(self):
        action = 0x0A90000000000000 | int(self.input["healing_action"], 16)
        _, ally, enemy = self.run.players
        cases = (
            (self.input["hero"], "healing_support_directed"),
            (self.input["healing_directed_hero"], "healing_friendly"),
        )
        for hero, expected in cases:
            self.run.match.switch_hero(self.speaker, heroes()[int(hero, 16)])
            self.speaker.skin = (int(self.input["skin"], 16), False)
            y = self.speaker.mover.position[1]
            self.speaker.mover.position[:] = [0.0, y, 0.0]
            self.speaker.mover.yaw = self.speaker.mover.pitch = 0
            ally.mover.position[:] = [0.0, y, 10.0]
            enemy.mover.position[:] = [10.0, y, 10.0]
            self.run.match.communication_action(self.speaker, {"+0x78": action, "+0x80": True})
            self.advance(130)
            self.assert_audience(self.expected[expected], [])
            for player in self.run.players:
                player.client.reliable.clear()


def spawned_body(hero: int):
    """A body of the hero whose full frame a fresh client model has applied, settled past SETTLE_FRAMES so
    the client is in step with the server (as test_script_driver does for Soldier)."""
    logging.getLogger("ow174.script").setLevel(logging.ERROR)
    logging.getLogger("ow174.game").setLevel(logging.ERROR)
    body = BodyScript(hero, 0xA0000101, START)
    client = decode.ClientBody(decode.initial_graphs(hero))
    [tree] = body.spawn_frames()
    client.apply(tree)
    body.stream.arrived(tree.chunk_last)
    frame = START
    for _ in range(SETTLE_FRAMES + 3):
        frame += 1
        body.command(frame, 0)
        for update in body.frames():
            client.apply(update)
            body.stream.arrived(update.chunk_last)
    return body, client, frame


def run_frames(body, client, frame, count):
    for _ in range(count):
        frame += 1
        body.command(frame, 0)
        for update in body.frames():
            client.apply(update)  # a crash raises decode.ClientCrash
            body.stream.arrived(update.chunk_last)
    return frame


class VoiceLineTests(unittest.TestCase):
    def test_some_heroes_have_spawn_lines(self):
        have = voicelines.heroes_with_lines()
        self.assertTrue(have, "no hero has a networked spawn voice stimulus")
        # Mercy is one of them (graph 113's stimulus), used by the round-trip test below.
        self.assertIn(hero_guid("Mercy"), have)
        self.assertLessEqual(len(have), len(graphs.bodies()))

    def test_spawn_line_reaches_the_client_and_ends_in_step(self):
        hero = hero_guid("Mercy")
        body, client, frame = spawned_body(hero)
        self.assertEqual(client.on(), server_on(body))  # in step before the line

        voice = voicelines.VoiceLines(StubMatch())
        voice.spawned(StubPlayer(body), now=0.0)
        self.assertEqual(len(voice.pending), 1)
        _, _, instance, index = voice.pending[0]
        key = (instance.id, index)
        self.assertEqual(body.component.instances[instance.id].states[index].node.cls, voicelines.VOICE_CLASS)

        frame = run_frames(body, client, frame, 3)
        self.assertIn(key, client.on())  # the client is playing the bark
        self.assertEqual(client.on(), server_on(body))

        voice.expire(now=voicelines.BARK_SECONDS + 1.0)  # the bark's time is up
        self.assertFalse(voice.pending)
        frame = run_frames(body, client, frame, 3)
        self.assertNotIn(key, client.on())  # and the client has stopped it
        self.assertEqual(client.on(), server_on(body))

    def test_every_hero_with_a_line_reaches_the_client(self):
        for hero, name in voicelines.heroes_with_lines().items():
            body, client, frame = spawned_body(hero)
            voice = voicelines.VoiceLines(StubMatch())
            voice.spawned(StubPlayer(body), now=0.0)
            self.assertEqual(len(voice.pending), 1, name)
            _, _, instance, index = voice.pending[0]
            frame = run_frames(body, client, frame, 3)
            self.assertIn((instance.id, index), client.on(), name)
            self.assertEqual(client.on(), server_on(body), name)

    def test_disabled_is_a_no_op(self):
        body, _, _ = spawned_body(hero_guid("Mercy"))
        voice = voicelines.VoiceLines(StubMatch())
        with mock.patch.object(voicelines, "VOICE_LINES", False):
            voice.spawned(StubPlayer(body), now=0.0)
        self.assertFalse(voice.pending)

    def test_a_hero_without_a_networked_stimulus_is_a_no_op(self):
        # Soldier: 76's only live voice graph is server-only, so the server has no bark for him.
        hero = hero_guid("Soldier: 76")
        self.assertNotIn(hero, voicelines.heroes_with_lines())
        body, _, _ = spawned_body(hero)
        voice = voicelines.VoiceLines(StubMatch())
        voice.spawned(StubPlayer(body), now=0.0)  # must not raise
        self.assertFalse(voice.pending)

    def test_a_retired_body_is_skipped(self):
        body, _, _ = spawned_body(hero_guid("Mercy"))
        body.retire()
        voice = voicelines.VoiceLines(StubMatch())
        voice.spawned(StubPlayer(body), now=0.0)
        self.assertFalse(voice.pending)


if __name__ == "__main__":
    unittest.main()
