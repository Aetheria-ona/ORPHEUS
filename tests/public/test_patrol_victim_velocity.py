"""A supported victim script can stop a floor-following placement for its next tick."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import bots
from ow174.game.match import Match
from ow174.game.mover import MoverData
from ow174.game.script import graph, play
from ow174.game.script.bot import BotScript
from ow174.game.script.driver import UPPERCUT_TARGET_GRAPH
from tests.script_fixtures import graph as synthetic_graph
from tests.script_fixtures import node, plug

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class PatrolVictimStopTests(unittest.TestCase):
    def test_velocity_action_stops_only_the_live_recipient_on_the_next_tick(self):
        contract = json.loads((FIXTURES / "patrol-victim-velocity-v1.json").read_text(encoding="utf-8"))
        inputs = contract["input"]
        for case in contract["cases"]:
            with self.subTest(case=case["id"]):
                velocity_action = node(1, "STU_FF3471E9", m_BC5E91CF=[{
                    "$": "STU_9AF0FA80", "m_A9561CA0": case["channel"],
                    "m_9B7A63EA": {"$": "STUConfigVarFloat", "m_value": case["magnitude"]},
                }])
                helper = synthetic_graph([
                    node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_inPlug"))), velocity_action,
                ], [0])
                helper.index = UPPERCUT_TARGET_GRAPH
                with (
                    patch.object(graph, "graph", return_value=helper),
                    patch.object(graph, "entity_body", return_value={"graphs": [], "manager": None}),
                    patch.object(bots, "mover_data", return_value=MoverData()),
                ):
                    self.exercise(inputs, case)

    def exercise(self, inputs, case):
        frame = inputs["arrival_tick"]
        spawner = {"hero": "1", "team": 1, "position": (0, 0, 0), "rotation": (0, 0, 0, 1),
                   "graphs": [hex(bots.PATROL)], "vars": {bots.ROUTE: [0, 1]}}
        hero = {"name": "synthetic", "body": "1", "health": 100}
        hints = {0: (0, 0, 0), 1: (0, 0, 20)}

        def placement():
            bot = bots.Bot(17, spawner, hero, hints)
            bot.body_script = BotScript(1, 1, bot.entity, frame - 4, 0, 100)
            bot.wait = 0
            bot.step(frame, 0.016)
            return bot

        bot = placement()
        self.assertGreater(bot.velocity[2], 0)
        match = Match.__new__(Match)
        sender = SimpleNamespace(
            body=11,
            spawned=True,
            has_body=True,
            client=True,
            body_script=SimpleNamespace(retired=False, component=SimpleNamespace(fault=None)),
        )
        match.tick, match.players, match.bots, match._body_messages = frame, [sender], [bot], []
        request = play.Request(play.Resource(graph.GRAPH_TYPE << 48 | UPPERCUT_TARGET_GRAPH), (bot.entity,),
                               play.InvocationContext(sender.body, sender.body, sender.body), (), 0, 0)
        match.queue_body_play(sender.body, request)
        if case["recipient"] == "replaced":
            match.bots = [placement()]
        elif case["recipient"] == "dead":
            bot.dead = True
        match.deliver_body_messages()
        target = match.bots[0]
        stop = target.body_script.component.world.patrol_stop_frame
        self.assertEqual(stop, case["stop_tick"])
        before = target.position
        target.step(frame + 1, 0.016, stop_frame=stop)
        self.assertEqual(target.position != before, case["moves_next"])
        after = target.position
        target.step(frame + 2, 0.016, stop_frame=stop)
        self.assertEqual(target.position != after, case["moves_again"])
