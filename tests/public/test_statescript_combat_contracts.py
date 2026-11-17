"""Public Junkenstein, charge, Deadeye aim-ray, and Flashbang entry-guard contracts.

Ported from the standalone contract helpers on ``rust`` (see #80):
- ``check_boss_speech_contract.py``
- ``check_charge_contract.py``
- ``check_deadeye_aim_ray_visibility_contract.py``
- ``check_flashbang_entry_guard_contract.py``
"""

import copy
import json
import math
import os
import struct
import unittest
from pathlib import Path
from random import Random
from types import SimpleNamespace
from unittest import mock

from ow174.game import collision, combat, junkenstein_ai, junkenstein_scene
from ow174.game.script import entity_team, expr, graph, nodes, runtime, scene
from tests.script_fixtures import graph as build_graph
from tests.script_fixtures import node, plug

_ = nodes  # Register StateScript node handlers.

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


def _load_contract(filename: str, case_id: str) -> dict:
    contract = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    if (contract["schema_version"], contract["case_id"], contract["target_build"]) != (
        1,
        case_id,
        104319,
    ):
        raise ValueError(f"unsupported contract header for {filename}: {contract['case_id']!r}")
    return contract


class StateScriptCombatContractTests(unittest.TestCase):
    def _run_boss_speech_case(self, contract: dict, case: dict) -> dict:
        owner = contract["input"]["owner"]

        def hex_int(key: str):
            return None if case.get(key) is None else int(case[key], 16)

        world = scene.SceneWorld()
        world.bind(owner, hero=hex_int("hero"))
        world.bind(runtime.GAME_MODE_ENTITY)
        world.bind_voice_set(runtime.GAME_MODE_ENTITY, hex_int("mode_set"))
        killer = contract["input"]["killer"]
        if hex_int("killer") is not None:
            world.bind(killer, hero=hex_int("killer"))
        component = runtime.Component(owner, strict=True, world=world)
        world.bind_script_component(owner, component)
        model = copy.deepcopy(contract["input"]["graph"])
        model["index"] = case["graph"]
        model["nodes"][0]["pos"] = case["node"]
        model["states"] = [case["node"]]
        active = component.create(graph.Graph(model), init=False)
        active.initialized = True
        active.write((0, 1), expr.Entity(killer) if hex_int("killer") is not None else None)
        state = active.state(0)
        notices = []

        def notice(anchor, kind, fields):
            self.assertEqual(kind, 27500)
            notices.append(
                {
                    "anchor": anchor,
                    "speaker": fields["+0x78"]["+0x0"],
                    "stimulus": f"0x{fields['+0x88']:016X}",
                    "line": f"0x{fields['+0x90']:016X}",
                }
            )

        player = SimpleNamespace(
            spawned=True,
            gone=False,
            has_body=True,
            team=0,
            body=contract["input"]["listener"],
            client=SimpleNamespace(queue_combat_notice=notice),
        )

        class _Draw(Random):
            def random(self):
                return struct.unpack(">d", bytes.fromhex(case["draw_bits"]))[0]

            def choice(self, rows):
                return rows[int(self.random() * len(rows))]

        with mock.patch.object(junkenstein_ai, "Random", _Draw):
            junkenstein_ai.bind_boss_voice(
                SimpleNamespace(players=[player]),
                component,
                hex_int("hero"),
                hex_int("skin"),
            )
        manager = world.voice_context(owner).lookup()
        definition = world.voice_set(owner)
        result = {"body_set": None if definition is None else f"0x{definition:016X}"}
        cue = hex_int("stimulus")
        stimulus = manager.load(cue)
        if stimulus is NotImplemented:
            return result | {"loaded": False}
        result.update(
            loaded=True,
            playable=stimulus.playable,
            mode=stimulus.mode,
            allowed=manager.allow(stimulus),
        )
        if not result["allowed"]:
            return result
        try:
            handle = manager.submit(state, stimulus, tuple(case["targets"]))
        except runtime.UnsupportedScriptError as error:
            return result | {"error": error.diagnostic["feature"]}
        duration = manager.duration(handle, cue, stimulus.mode)
        self.assertIsNot(duration, NotImplemented)
        return result | {
            "handle": handle,
            "notices": notices,
            "duration_bits": struct.pack(">d", duration).hex(),
            "part": manager.part(state, 1),
        }

    def test_boss_speech_contract(self):
        contract = _load_contract("junkenstein-boss-speech-v1.json", "junkenstein.boss-speech.v1")
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                self.assertEqual(self._run_boss_speech_case(contract, case), result["output"])

    def _run_charge_case(self, contract: dict, case: dict) -> dict:
        owner, target = contract["input"]["owner"], contract["input"]["target"]
        world = scene.SceneWorld()
        world.mode = int(contract["input"]["mode"], 16)
        if case["kind"] == "corpse":
            controller = target + 1
            world.bind(target)
            world.bind(controller)
            world.possessors = set()
            world.set_possessor(controller, True)
            component = runtime.Component(target, strict=True, world=world)
            component.owner_dead = case["death_frame"] is not None
            world.bind_script_component(target, component)
            world.set_linked_controller(target, controller)
            world.set_current_possession(controller, target)
            world.ai_behavior_requests[controller] = {}
            actor = SimpleNamespace(
                entity=target,
                hero=int(case["hero"], 16),
                dead=component.owner_dead,
            )
            host = SimpleNamespace(
                world=world,
                frame=case["frame"],
                frame_ms=16,
                player_bindings={},
                combatants={target: (actor, None)},
                match=SimpleNamespace(
                    players=[],
                    combat=SimpleNamespace(
                        vitals={target: SimpleNamespace(death_tick=case["death_frame"])}
                    ),
                ),
                combatant_component=lambda _actor: component,
            )
            junkenstein_scene.JunkensteinSceneHost.before_units(host)
            return {"retire": target in world.corpse_retirements}

        world.bind(owner, definition=0x04000000000026F0)
        world.bind(target, target_tags=frozenset(int(v, 16) for v in case.get("target_tags", [])))
        if case.get("context", True):
            flags = {
                owner: case.get("source_flags", 0x800001),
                target: case.get("target_flags", 0x1000001),
            }
            context = entity_team.Context(
                owner,
                True,
                True,
                True,
                lookup=lambda entity, _flags=flags: (
                    entity_team.Target(entity, entity_team.FilterBits(_flags[entity]))
                    if entity in _flags
                    else None
                ),
                watch_filter=None,
                watch_environment=None,
            )
            world.set_entity_team_context(owner, context)
        component = runtime.Component(owner, strict=True, world=world)
        world.bind_script_component(owner, component)
        model = copy.deepcopy(contract["input"]["graph"])
        initial = {338: 200.0, 342: 0.0, 2426: False, 842: False}
        initial.update({int(k): v for k, v in case.get("initial", {}).items()})
        values = {(1, k): v for k, v in initial.items()} | {(0, 393): expr.Entity(target)}
        active = component.create(graph.Graph(model), values=values)
        victim = runtime.Component(target, strict=True, world=world)
        world.bind_script_component(target, victim)
        victim.set(602, case.get("no_charge", False))
        component.run_now()

        def decode_item(item):
            if isinstance(item, dict) and "asset" in item:
                return expr.Asset(int(item["asset"], 16))
            if isinstance(item, dict) and "entity" in item:
                return expr.Entity(item["entity"])
            return item

        def charge_bits():
            return struct.pack(">d", float(component.entity_var(342).value())).hex()

        if case["kind"] == "tags":
            active.write((0, 1), decode_item(case.get("variable")))
            hit = combat.hit_def(active.state(0), case["hit"])
            return {"tags": [f"0x{v:016X}" for v in hit.tags]}
        if case["kind"] == "query":
            try:
                result = active.evaluate(case["query"])
            except runtime.UnsupportedScriptError as error:
                self.assertIn("STU_A58C1E63", str(error))
                return {"error": "STU_A58C1E63"}
            return {"hostile": result}
        results = []
        last_frame = 0
        for step in case["steps"]:
            if step["op"] == "set":
                for key, selected in step["values"].items():
                    component.set(int(key), selected)
                component.run_now()
            elif step["op"] == "advance":
                for frame in range(last_frame + 1, step["frame"] + 1):
                    component.run_frame(frame)
                if step["frame"] == last_frame:
                    component.run_frame(last_frame)
                last_frame = step["frame"]
            elif step["op"] == "poll":
                component.run_now()
            elif step["op"] == "damage":
                runtime.send_instance_message(
                    active,
                    0x0240000000000012,
                    {0x13: float(step["amount"]), 0x15: expr.Entity(target)},
                    owner,
                )
                component.run_now()
            else:
                self.assertEqual(step["op"], "observe")
            results.append({"charge_bits": charge_bits(), "waiting": active.state(4).active})
        return {"steps": results}

    def test_charge_contract(self):
        contract = _load_contract("statescript-charge-v1.json", "statescript.charge.v1")
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                self.assertEqual(self._run_charge_case(contract, case), result["output"])

    def test_deadeye_aim_ray_visibility_contract(self):
        contract = _load_contract(
            "deadeye-aim-ray-visibility-v1.json",
            "deadeye.aim-ray-visibility.v1",
        )
        origin, target = contract["input"]["origin"], contract["input"]["target"]
        x, y, z = target["feet"]
        radius, height = target["radius"], target["height"]
        a, b = (x, y + radius, z), (x, y + height - radius, z)
        point = (x, min(y + target["eye"], b[1]), z)
        delta = tuple(p - q for p, q in zip(point, origin, strict=True))
        length = math.hypot(*delta)
        direction = tuple(value / length for value in delta)
        reach = combat.ray_capsule(origin, direction, a, b, radius)
        self.assertIsNotNone(reach)
        for case in contract["input"]["cases"]:
            with self.subTest(case=case["name"]):
                triangles = [
                    tuple(value for pt in triangle for value in pt) for triangle in case["triangles"]
                ]
                world = collision.RayWorld(triangles, [0x18] * len(triangles))
                wall = combat.ray_world(world, origin, direction, reach, 0x18)
                actual = {
                    "visible": wall is None or wall + 1e-9 >= reach,
                    "legacy_visible": combat.capsule_visible(world, origin, a, b, radius, 0x18),
                }
                self.assertEqual(actual, case["expected"])

    def test_flashbang_entry_guard_contract(self):
        contract = _load_contract("flashbang-entry-guard-v1.json", "flashbang.entry-guard.v1")
        for case in contract["cases"]:
            with self.subTest(case=case["name"]):
                if case.get("error"):
                    self.skipTest("Rust-only adapter availability boundary")
                linked = case["stack_value"] is not None
                spec_nodes = [
                    node(
                        0,
                        "STUStatescriptEntry",
                        m_outPlug=plug(*([(1, "m_beginPlug")] if linked else [])),
                    )
                ]
                if linked:
                    spec_nodes.append(
                        node(
                            1,
                            "STUStatescriptStateStack",
                            state=0,
                            m_out_Var={
                                "$": "STUConfigVarDynamic",
                                "m_60DB8F99": 1,
                                "m_identifier": 2547,
                            },
                            m_value={"$": "STUConfigVarBool", "m_value": case["stack_value"]},
                            m_priority={"$": "STUConfigVarFloat", "m_value": 0},
                        )
                    )
                component = runtime.Component(42, strict=True)
                active = component.create(build_graph(spec_nodes, entries=(0,)))
                active.write((1, 2547), case["base"])
                active.write((0, 29458), case["force_default"])
                component.run_now()
                actual = active.evaluate(contract["condition"])
                self.assertEqual(actual, case["accepted"])
