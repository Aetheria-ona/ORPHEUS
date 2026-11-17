"""Public StateScript VM, array, stack, and voice behavior contracts.

Ported from the standalone contract helpers on ``rust`` (see #80):
- ``check_ability_press_window_contract.py``
- ``check_array_insert_contract.py``
- ``check_contains_contract.py``
- ``check_corpse_retirement_contract.py``
- ``check_engine_setting_contract.py``
- ``check_stack_admission_contract.py``
- ``check_victory_view_route_contract.py``
- ``check_voice_lifecycle_contract.py``
"""

import copy
import json
import math
import os
import struct
import unittest
from pathlib import Path

from ow174.game.script import expr, graph, nodes, runtime, scene, voice_stimulus
from tests.public import test_script_array_insert as array_insert
from tests.script_fixtures import graph as build_graph
from tests.script_fixtures import node, plug, setter

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


def _dynamic_var(identifier: int, *, boolean_flag: bool = False) -> dict:
    return {
        "$": "STUConfigVarDynamic",
        "m_identifier": identifier,
        "m_60DB8F99": True if boolean_flag else 1,
    }


def _decode_value(value):
    if isinstance(value, list):
        return tuple(_decode_value(item) for item in value)
    if isinstance(value, dict):
        if set(value) == {"entity"}:
            return expr.Entity(value["entity"])
        if set(value) == {"float"} and value["float"] in ("NaN", "Infinity"):
            return float(value["float"])
        if set(value) == {"asset"}:
            return expr.Asset(int(value["asset"], 16))
        raise ValueError(f"unsupported contract value: {value!r}")
    return value


def _encode_value(value):
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    if isinstance(value, expr.Entity):
        return {"entity": value.id}
    if isinstance(value, float) and not math.isfinite(value):
        return {"float": "NaN" if math.isnan(value) else "Infinity"}
    if value is None or type(value) in (bool, int, float):
        return value
    raise ValueError(f"unsupported implementation result: {value!r}")


class StateScriptVmContractTests(unittest.TestCase):
    def test_ability_press_window_contract(self):
        contract = _load_contract(
            "statescript-ability-press-window-v1.json",
            "statescript.ability-press-window.v1",
        )
        for case in contract["input"]["cases"]:
            with self.subTest(case=case["name"]):
                model = build_graph(
                    [
                        node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_beginPlug"))),
                        node(
                            1,
                            "STUStatescriptStateAbility",
                            state=0,
                            m_A5F1A77B=36,
                            m_246438AD=contract["input"]["window_seconds"],
                            m_150F0D92=_dynamic_var(900),
                            m_EF3C5C73=[{"m_out_Var": _dynamic_var(902), "m_value": True}],
                        ),
                    ],
                    entries=(0,),
                )
                model.fields["m_680A2CB2"] = [0]
                component = runtime.Component(42, strict=True)
                active = component.create(model)
                active.write((1, 900), False)
                active.write((1, 902), False)
                component.run_now()
                previous, observed = False, []
                for frame in range(1, contract["input"]["frames"] + 1):
                    if frame == case["ready_frame"]:
                        active.write((1, 900), True)
                    pressed = frame in case["press_frames"]
                    component.run_frame(frame, [(36, pressed)] if pressed != previous else [])
                    previous = pressed
                    if frame in contract["input"]["sample_frames"]:
                        observed.append(bool(active.find_var(1, 902).value()))
                self.assertEqual(observed, case["expected"])

    def test_array_insert_contract(self):
        contract = _load_contract(
            "statescript-array-insert-v1.json",
            "statescript.unkeyed-array-insert.v1",
        )
        self.assertEqual(contract["input"]["output_scope"], "entity")
        cases = contract["input"]["cases"]
        expected = contract["expected"]["states"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                options = {
                    "value": _decode_value(case["source"]),
                    "unique": case["unique"],
                    "output": array_insert.dynamic(100, scope=1),
                    "action_class": "STU_CBE8BDAA" if case["single"] else "STU_4C3509FB",
                }
                if case["index_present"]:
                    options["index"] = _decode_value(case["index"])
                if "source_config" in case:
                    options["source"] = case["source_config"]
                component, active, action = array_insert.setup(**options)
                for field, native_field in (("index_config", "m_index"), ("key_config", "m_key")):
                    if field in case:
                        action.fields[native_field] = case[field]
                active.write((1, 100), _decode_value(case["current"]))
                active.run_node(action, None)
                actual = _encode_value(component.entity_var(100).value())
                marker = active.find_var(0, 90, create=False)
                followed = marker is not None and marker.value() == 19
                self.assertEqual(
                    json.dumps(actual, sort_keys=True),
                    json.dumps(result["value"], sort_keys=True),
                )
                self.assertEqual(followed, result["followed"])

    def test_container_contains_contract(self):
        contract = _load_contract(
            "statescript-contains-v1.json",
            "statescript.container-contains-condition.v1",
        )

        def entity_setter(position: int, value: int) -> dict:
            return node(
                position,
                "STUStatescriptActionSetVar",
                m_out_Var=_dynamic_var(90, boolean_flag=True),
                m_value={"$": "STUConfigVarInt", "m_value": value},
            )

        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                model = build_graph(
                    [
                        node(0, "STUStatescriptEntry", m_outPlug=plug((1, "m_inPlug"))),
                        node(
                            1,
                            "STU_821116A6",
                            m_814F8EC3=_dynamic_var(100, boolean_flag=True),
                            m_value=_dynamic_var(101, boolean_flag=True),
                            m_truePlug=plug(*([(2, "m_inPlug")] if case["links"] else [])),
                            m_falsePlug=plug(*([(3, "m_inPlug")] if case["links"] else [])),
                        ),
                        entity_setter(2, 19),
                        entity_setter(3, 23),
                    ],
                    entries=(0,),
                )
                component = runtime.Component(123, strict=True)
                active = component.create(model, init=False)
                active.initialized = True
                component.set(100, _decode_value(case["container"]))
                component.set(101, _decode_value(case["value"]))
                active.run_node(model.node(0), None)
                marker = component.entity_var(90, create=False)
                actual = marker.value() if marker is not None else None
                self.assertEqual(actual, result["marker"])

    def test_corpse_retirement_contract(self):
        contract = _load_contract(
            "statescript-corpse-retirement-v1.json",
            "statescript.corpse-retirement-action.v1",
        )

        class _Host(runtime.World):
            def __init__(self, accept: bool) -> None:
                self.accept = accept
                self.requests = []

            def request_corpse_retirement(self, instance, entity, *, phase_exit=False):
                self.requests.append([entity, phase_exit])
                return self.accept

        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                position = case["position"]
                model = build_graph(
                    [
                        node(0, "STUStatescriptEntry", m_outPlug=plug((position, "m_inPlug"))),
                        node(
                            position,
                            "STU_523EE38D",
                            m_entity=_dynamic_var(1, boolean_flag=True),
                            m_outPlug=plug((99, "m_inPlug")),
                        ),
                        node(
                            99,
                            "STUStatescriptActionSetVar",
                            m_out_Var=_dynamic_var(90, boolean_flag=True),
                            m_value={"$": "STUConfigVarBool", "m_value": True},
                        ),
                    ],
                    entries=(0,),
                )
                model.index = case["graph"]
                host = _Host(case["accept"])
                component = runtime.Component(contract["input"]["owner"], strict=True, world=host)
                active = component.create(model, init=False)
                active.initialized = True
                target = case["target"]
                component.set(1, expr.Entity(target["entity"]) if isinstance(target, dict) else target)
                failed = False
                try:
                    active.run_node(model.node(0), None)
                except runtime.UnsupportedScriptError:
                    failed = True
                marker = component.entity_var(90, create=False)
                actual = {
                    "name": case["name"],
                    "requests": host.requests,
                    "followed": marker is not None and marker.value() is True,
                    "failed": failed,
                }
                self.assertEqual(actual, result)

    def test_engine_setting_read_contract(self):
        contract = _load_contract(
            "statescript-engine-setting-v1.json",
            "statescript.engine-setting-read.v1",
        )
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld()
                settings = case["settings"]
                world.engine_settings = (
                    None
                    if settings is None
                    else {key: _decode_value(value) for key, value in settings}
                )
                _, active, _ = array_insert.setup(world=world)
                config = {"$": "STU_A753DC79", "m_EE729DCB": 0, "m_74A2248F": case["hash"]}
                if "error" in result:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, result["error"]):
                        active.evaluate(config, initial=_decode_value(case["initial"]))
                else:
                    actual = active.evaluate(config, initial=_decode_value(case["initial"]))
                    self.assertEqual(
                        json.dumps(_encode_value(actual), sort_keys=True),
                        json.dumps(result["value"], sort_keys=True),
                    )

    def test_stack_admission_contract(self):
        contract = _load_contract(
            "statescript-stack-admission-v1.json",
            "statescript.stack-admission.v1",
        )
        target = _dynamic_var(900)
        for case in contract["input"]["cases"]:
            with self.subTest(case=case["name"]):
                spec_nodes = [node(1, "STUStatescriptState", state=0)]
                starts, stops = [(1, "m_beginPlug")], []
                for index, priority in enumerate(case["priorities"]):
                    pos = index + 2
                    spec_nodes.append(
                        node(
                            pos,
                            "STUStatescriptStateStack",
                            state=pos - 1,
                            m_out_Var=target,
                            m_value=case["values"][index],
                            m_priority={"m_priority": priority},
                        )
                    )
                    starts.append((pos, "m_beginPlug"))
                    stops.append((pos, "m_F198FD3A"))
                spec_nodes.append(node(0, "STUStatescriptEntry", m_outPlug=plug(*starts)))
                message = len(spec_nodes)
                spec_nodes.append(
                    node(
                        message,
                        "STUStatescriptEntryGameMessage",
                        m_gameMessage={"m_gameMessage": "0x0240000000000001"},
                        m_outPlug=plug(*stops),
                    )
                )
                model = build_graph(spec_nodes, entries=(0, message))
                component = runtime.Component(42, strict=True)
                component.create(model)
                component.run_now()
                if case["release"]:
                    runtime.send_message(component, 0x0240000000000001, {}, 42)
                    component.run_now()
                active = next(iter(component.instances.values()))
                actual = active.evaluate(
                    {
                        "$": "STU_3ACE35FB",
                        "m_E50CF556": target,
                        "m_012B7AF7": {"m_priority": contract["input"]["candidate"]},
                    }
                )
                self.assertEqual(actual, case["expected"])

    def test_victory_view_route_contract(self):
        contract = _load_contract(
            "statescript-victory-view-route-v1.json",
            "statescript.victory-view-route.v1",
        )

        class _RecordingWorld(scene.SceneWorld):
            def __init__(self) -> None:
                super().__init__(mode=0x0230000000000003)
                self.requests = []

            def submit_game_message(self, instance, node_obj, request) -> None:
                self.requests.append(request)

        for case in contract["input"]["cases"]:
            with self.subTest(case=case["name"]):
                world = _RecordingWorld()
                owner, subject = runtime.GAME_MODE_ENTITY, contract["input"]["subject"]
                world.bind(owner)
                world.bind(subject)
                receiver = runtime.Component(subject, world=world, strict=True)
                world.bind_script_component(subject, receiver)
                receiver_graph = build_graph([node(0, "STUStatescriptState", state=0)])
                receiver_graph.index = 0x0CA5
                receiver.create(receiver_graph)
                component = runtime.Component(owner, world=world, strict=True)
                world.bind_script_component(owner, component)
                fields = dict(case["fields"])
                fields["m_outPlug"] = plug((5, "m_inPlug"))
                following = setter(5, 1)
                following["fields"]["m_out_Var"]["m_identifier"] = 9
                model = build_graph([node(case["position"], case["class"], **fields), following])
                model.index = 0x0CEC
                active = component.create(model, init=False)
                active.initialized = True
                active.write((0, 8602), expr.Entity(subject))
                active.run_node(model.node(case["position"]), None)
                if not world.requests:
                    actual = {"forwarded": bool(active.find_var(0, 9).value()), "parameters": None}
                else:
                    (request,) = world.requests
                    self.assertEqual(request.targets, (subject,))
                    self.assertFalse(request.self_target)
                    self.assertEqual(request.owner, owner)
                    actual = {
                        "forwarded": bool(active.find_var(0, 9).value()),
                        "parameters": {
                            str(key & 0xFFFF): _encode_value(values[0])
                            for key, values in request.params
                            if len(values) == 1
                        },
                    }
                self.assertEqual(actual, case["expected"])

    def _run_voice_case(self, contract: dict, case: dict) -> list[dict]:
        def number(value):
            return float(value["float"]) if isinstance(value, dict) and "float" in value else value

        def encoded(value):
            return {"bits64": struct.pack(">d", value).hex()} if type(value) is float else value

        owner = contract["input"]["owner"]
        world = scene.SceneWorld()
        world.bind(owner)
        component = runtime.Component(owner, strict=True, world=world)
        world.bind_script_component(owner, component)
        model = copy.deepcopy(contract["input"]["graph"])
        model["nodes"][0]["fields"].update(case.get("fields", {}))
        active = component.create(graph.Graph(model), init=False)
        active.initialized = True
        for key in (80, 81, 82, 83):
            active.write((0, key), 0)
        active.write((0, 1), _decode_value(case.get("targets", [{"entity": owner}])))
        for key, selected in case.get("variables", {}).items():
            active.write((0, int(key)), _decode_value(selected))
        state = active.state(0)
        events, now, cursors = [], [number(case.get("clock", 100.0))], {}

        def record(event, result):
            events.append(event)
            return result

        def selected(key, sequence):
            index = cursors.get(key, 0)
            cursors[key] = index + 1
            return sequence[min(index, len(sequence) - 1)]

        def clock():
            return record(["clock"], NotImplemented if now[0] is None else now[0])

        def load(guid):
            availability = selected("resource", case.get("resource", ["present"]))
            res = (
                voice_stimulus.Stimulus(
                    guid,
                    case.get("playable", True),
                    case.get("mode", False),
                )
                if availability == "present"
                else None
                if availability == "absent"
                else NotImplemented
            )
            return record(["load", f"0x{guid:016X}"], res)

        def duration(handle, guid, mode):
            res = number(selected("duration", case.get("duration", [0.25])))
            return record(["duration", handle, mode], NotImplemented if res is None else res)

        def part(st, index):
            res = number(selected(f"part-{index}", case.get("parts", {}).get(str(index), [0.0])))
            return record(["part", index], NotImplemented if res is None else res)

        manager = voice_stimulus.Manager(
            load=load,
            allow=lambda stimulus: record(["allow", stimulus.mode], case.get("allow", True)),
            submit=lambda st, stimulus, targets: record(
                ["submit", list(targets)], case.get("handle", 17)
            ),
            duration=duration,
            part=part,
        )
        availability = case.get("manager", "present")
        context = voice_stimulus.Context(
            component,
            clock=clock,
            lookup=lambda: record(
                ["manager"],
                manager
                if availability == "present"
                else None
                if availability == "absent"
                else NotImplemented,
            ),
        )
        host = case.get("context", "present")
        if host != "unavailable":
            world.set_voice_context(owner, context if host == "present" else None)
        original_context = world.voice_context
        world.voice_context = lambda entity: record(["context"], original_context(entity))
        actual_steps = []
        for step in case["steps"]:
            if "clock" in step:
                now[0] = number(step["clock"])
            outcome = None
            try:
                op = step["op"]
                if op == "begin":
                    state.begin()
                elif op == "advance":
                    component.frame = step["frame"]
                    component.now = step["frame"] * 16
                    component.update()
                elif op == "drain":
                    component.run_now()
                elif op == "poll":
                    context.poll()
                elif op == "failure":
                    outcome = context.notify_failure(state)
                elif op in ("abort", "finish"):
                    state.end(op == "finish")
                elif op == "destroy":
                    component.destroy(active)
                elif op == "set_output":
                    active.write((0, 79), float(step["value"]))
                elif op == "timer":
                    state.timer(step["parameter"])
                else:
                    raise ValueError(f"unknown step {op}")
            except runtime.UnsupportedScriptError as error:
                actual_steps.append(
                    {
                        "error": error.diagnostic["feature"],
                        "class": error.diagnostic["class"],
                        "events": events[:],
                    }
                )
                break
            exists = active.id in component.instances
            cache = None
            if exists and state.voice_started_at is not None:
                cache = {
                    name.removeprefix("voice_"): encoded(getattr(state, name))
                    for name in voice_stimulus.CACHES
                }
            values = None
            if exists:
                values = {}
                for key in (79, 80, 81, 82, 83):
                    variable = active.find_var(0, key, create=False)
                    values[str(key)] = None if variable is None else encoded(variable.value())
            actual_steps.append(
                {
                    "events": events[:],
                    "exists": exists,
                    "active": state.active if exists else None,
                    "registered": len(context.registered),
                    "voice": cache,
                    "values": values,
                    "outcome": outcome,
                }
            )
            events.clear()
        return actual_steps

    def test_voice_lifecycle_contract(self):
        contract = _load_contract(
            "statescript-voice-lifecycle-v1.json",
            "statescript.voice-lifecycle.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                self.assertEqual(self._run_voice_case(contract, case), expected["steps"])
