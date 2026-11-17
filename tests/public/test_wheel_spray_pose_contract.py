"""Spray rays use the command pose associated with the confirmed wheel action."""

import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ow174.game import messages
from ow174.game.bits import BitWriter
from ow174.game.environment import SystemEnvironment
from ow174.game.match import Match
from ow174.game.mover import CROUCHED
from ow174.game.script import runtime
from ow174.game.script.driver import BodyWorld
from ow174.game.server import COMMUNICATION_ACTION, Client

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class WheelSprayPoseContractTests(unittest.TestCase):
    def test_confirmed_spray_uses_one_pose_for_origin_and_aim(self):
        contract = json.loads((FIXTURES / "wheel-spray-pose-v1.json").read_text(encoding="utf-8"))
        inputs = contract["input"]
        history, current = inputs["history"], inputs["current"]
        historical_pose = SimpleNamespace(
            frame=history["frame"], position=history["position"], yaw=history["yaw"], pitch=0,
            flags=CROUCHED if history["crouched"] else 0, spring=(history["spring_step"], 0),
        )
        for case, expected in zip(inputs["cases"], contract["expected"], strict=True):
            with self.subTest(case=case["name"]):
                match = Match.__new__(Match)
                match._body_messages = []
                rays = SimpleNamespace(cast_ray=Mock(return_value=None))
                match.collision_world = SimpleNamespace(ray_geometry=rays)
                match.combat = SimpleNamespace(is_dead=lambda _: False)
                world = BodyWorld(0, 0, 0xA0000101, 0)
                world.match = match
                world.communication_wheel = True
                script = SimpleNamespace(
                    world=world, retired=False, entity=world.body, component=SimpleNamespace(fault=None),
                )
                mover = SimpleNamespace(
                    **current, pitch=0, history=[historical_pose],
                    spring_low=inputs["spring_range"][0], spring_high=inputs["spring_range"][1],
                )
                player = SimpleNamespace(
                    name="Test", body=world.body, match=match, spawned=True, has_body=True,
                    body_script=script, mover=mover, hero=SimpleNamespace(body=0),
                    commands=SimpleNamespace(last_frame=case["last_frame"]),
                )
                match.players = [player]
                server = SimpleNamespace(environment=SystemEnvironment())
                connection = Client(server, ("127.0.0.1", 1), 1, None, player)
                player.client = connection
                player.take_commands = Mock(return_value=None)

                for seq, confirmed, frame in case["packets"]:
                    selection = {"+0x78": int(inputs["selection"], 16), "+0x80": confirmed}
                    reliable = [(seq, COMMUNICATION_ACTION, selection)]
                    packet = BitWriter()
                    packet.bit(frame is not None)
                    if frame is not None:
                        command = BitWriter()
                        command.bits(0, 24)  # latency and key flags
                        command.bits(1, 8)
                        command.bits(frame, 32)
                        command.bits(0, 1 + 32 + 24 + 8 + 16)  # axes, aim, buttons, action and extra
                        packet.bits(command.count, 32)
                        packet.append(command)
                    with patch.object(messages, "read_channel", return_value=(reliable, [])):
                        connection._payload_in(packet.getvalue(), 17.0)
                    if frame is not None:
                        [commands], _ = player.take_commands.call_args
                        self.assertEqual([command.frame for command in commands], [frame])
                        player.commands.last_frame = frame

                def receive_selection(_component, _message, params, _source, body_world=world):
                    if params[0x3E1]:
                        body_world.resolve_spray(inputs["length"])
                    return 0

                with patch.object(runtime, "send_message", side_effect=receive_selection):
                    match.deliver_body_messages()
                rays.cast_ray.assert_called_once()
                origin, direction, length, mask = rays.cast_ray.call_args.args
                expected_ray = (*expected["origin"], *expected["direction"])
                for actual, target in zip((*origin, *direction), expected_ray, strict=True):
                    self.assertAlmostEqual(actual, target, places=6)
                self.assertEqual((length, mask), (inputs["length"], 0x40))
                self.assertIsNone(world.spray_command_frame)
