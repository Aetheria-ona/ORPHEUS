"""Public StateScript entity, team-filter, health, velocity, and foreign-slot contracts.

Ported from the standalone contract helpers on ``rust`` (see #80):
- ``check_ally_filter_contract.py``
- ``check_foreign_component_contract.py``
- ``check_foreign_notification_contract.py``
- ``check_health_total_contract.py``
- ``check_hostile_filter_contract.py`` (covers both hostile-filter and combat-target)
- ``check_retired_entity_contract.py``
- ``check_velocity_contract.py``
"""

import json
import os
import struct
import unittest
from pathlib import Path
from unittest import mock

from ow174.game.script import entity_team, expr, graph, nodes, runtime, scene
from tests.public import test_script_array_insert as array_insert

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


def _decode_value(value):
    if isinstance(value, list):
        return tuple(_decode_value(item) for item in value)
    if isinstance(value, dict):
        if set(value) == {"entity"}:
            return expr.Entity(value["entity"])
        if set(value) == {"vector"}:
            return expr.Vec3(*value["vector"])
        if set(value) == {"bits64"}:
            return struct.unpack(">d", bytes.fromhex(value["bits64"]))[0]
        if set(value) == {"float"}:
            return float(value["float"])
        raise ValueError(f"invalid contract value {value!r}")
    return value


def _encode_value(value):
    if isinstance(value, expr.Vec3):
        return {"vector": list(value)}
    if isinstance(value, tuple):
        return [_encode_value(item) for item in value]
    return value


class StateScriptEntityContractTests(unittest.TestCase):
    def test_ally_filter_contract(self):
        contract = _load_contract(
            "statescript-ally-filter-v1.json",
            "statescript.ally-filter.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld(mode=next(iter(scene.JUNKENSTEIN_MODES)))
                world.bind(123, position=(0, 0, 0), target_tags=[])
                component, active, _ = array_insert.setup(world=world)
                active.graph.index = case["graph"]
                world.bind_script_component(123, component)
                flags = {123: case["source_flags"]}
                for body in case["bodies"]:
                    world.bind(
                        body["entity"],
                        position=body["position"],
                        target_tags=(
                            None
                            if body["tags"] is None
                            else [0x0D60000000000000 + tag for tag in body["tags"]]
                        ),
                    )
                    flags[body["entity"]] = body["flags"]
                world.array_filter_inputs = lambda entity, _flags=flags: (True, _flags[entity])

                def lookup(entity, *, _flags=flags):
                    return entity_team.Target(entity, entity_team.FilterBits(_flags[entity]))

                for entity in flags:
                    world.set_entity_team_context(
                        entity,
                        entity_team.Context(entity, True, True, True, lookup=lookup),
                    )
                query = (
                    {"$": "STU_B3E67A36", "m_filter": case["filter"]}
                    if case["kind"] == "first"
                    else {
                        "$": "STU_AC829876",
                        "m_filter": case["filter"],
                        "m_entity": {
                            "$": "STUConfigVarDynamic",
                            "m_identifier": 2,
                            "m_60DB8F99": False,
                        },
                    }
                )
                active.write((0, 2), expr.Entity(case["target"]))
                outer = {
                    8: expr.Entity(999),
                    9: expr.Entity(case["parameter9"]),
                    10: expr.Entity(124),
                }
                if "error" in expected:
                    with (
                        self.assertRaises(runtime.UnsupportedScriptError) as raised,
                        component.parameter_scope(outer),
                    ):
                        try:
                            active.evaluate(query)
                        finally:
                            self.assertEqual(component.parameter_scopes, [outer])
                    self.assertIn(expected["error"], str(raised.exception))
                else:
                    with component.parameter_scope(outer):
                        actual = active.evaluate(query)
                        self.assertEqual(component.parameter_scopes, [outer])
                    wanted = (
                        expr.Entity(expected["value"]) if case["kind"] == "first" else expected["value"]
                    )
                    self.assertEqual(actual, wanted)
                    self.assertIs(type(actual), type(wanted))
                self.assertFalse(component.parameter_scopes)

    def test_foreign_component_read_contract(self):
        contract = _load_contract(
            "statescript-foreign-component-v1.json",
            "statescript.foreign-component-read.v1",
        )
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld()
                target = contract["input"]["target"]
                world.bind(target)
                state = case["state"]
                if state == "absent":
                    world.bind_script_component(target, None)
                elif state in ("running", "faulted"):
                    component = runtime.Component(target, strict=True, world=world)
                    for key, value in case["variables"].items():
                        component.set(int(key), _decode_value(value))
                    world.bind_script_component(target, component)
                    if state == "faulted":
                        component.fault = RuntimeError("synthetic faulted component")
                elif state != "unavailable":
                    raise ValueError(f"unknown component state {state}")
                _, active, _ = array_insert.setup(world=world, current=expr.Entity(target))
                if "error" in result:
                    self.assertEqual(result["error"], "unavailable_entity_variable")
                    with self.assertRaisesRegex(
                        runtime.UnsupportedScriptError, "entity variable service"
                    ):
                        active.evaluate(contract["input"]["config"])
                else:
                    actual = active.evaluate(contract["input"]["config"])
                    self.assertEqual(json.dumps(_encode_value(actual)), json.dumps(result["value"]))

    def test_foreign_notification_contract(self):
        contract = _load_contract(
            "statescript-foreign-notification-v1.json",
            "statescript.foreign-notification.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        records = {record["index"]: record for record in contract["input"]["graphs"]}
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld()
                owner, target = contract["input"]["owner"], contract["input"]["target"]
                world.bind(owner)
                world.bind(target)
                foreign = runtime.Component(target, strict=True, world=world)
                foreign.set(6604, case["initial"])
                world.bind_script_component(target, foreign)
                component = runtime.Component(owner, strict=True, world=world)
                world.bind_script_component(owner, component)
                with mock.patch.object(graph, "_records", lambda: records):
                    active = component.create(
                        graph.graph(0x0C8C), values={(0, 1): expr.Entity(target)}
                    )
                    component.run_now()

                    def snapshot(
                        *, _active=active, _component=component, _foreign=foreign
                    ):
                        return {
                            "active": _active.state(0).active,
                            "complete": bool(_component.entity_var(9).value()),
                            "children": sum(
                                item.graph.index == 0x7000
                                for item in _component.instances.values()
                            ),
                            "subscriptions": len(_foreign.entity_var(6604).watchers),
                        }

                    actual = [snapshot()]
                    for step in case["steps"]:
                        if step["op"] == "set":
                            foreign.set(6604, step["value"])
                        elif step["op"] == "abort":
                            active.state(0).request(runtime.ABORT)
                        else:
                            self.assertEqual(step["op"], "poll")
                        component.run_frame(step["frame"])
                        actual.append(snapshot())
                    self.assertEqual(actual, expected["output"])

    def test_total_health_read_contract(self):
        contract = _load_contract(
            "statescript-health-total-v1.json",
            "statescript.total-health-read.v1",
        )
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                world = runtime.World() if case["table"] is None else scene.SceneWorld()
                for entity, health in case["table"] or ():
                    if health is None:
                        callback = None
                    else:
                        decoded = _decode_value(health)
                        callback = lambda v=decoded: v  # noqa: E731
                    world.bind(entity, health=callback)
                _, active, _ = array_insert.setup(world=world, current=_decode_value(case["target"]))
                config = {
                    "$": "STU_9B7648C3",
                    "m_EE729DCB": 0,
                    "m_entity": case.get(
                        "selector",
                        {"$": "STUConfigVarDynamic", "m_identifier": 1, "m_60DB8F99": False},
                    ),
                }
                if "error" in result:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, result["error"]):
                        active.evaluate(config, initial=_decode_value(case["initial"]))
                else:
                    actual = active.evaluate(config, initial=_decode_value(case["initial"]))
                    self.assertEqual(
                        json.dumps(_encode_value(actual), sort_keys=True),
                        json.dumps(_encode_value(_decode_value(result["value"])), sort_keys=True),
                    )

    def test_hostile_filter_contract(self):
        contract = _load_contract(
            "statescript-hostile-filter-v1.json",
            "statescript.hostile-filter.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld(mode=next(iter(scene.JUNKENSTEIN_MODES)))
                world.bind(123)
                world.bind(
                    124,
                    target_tags=(
                        None
                        if case["tags"] is None
                        else [0x0D60000000000000 + tag for tag in case["tags"]]
                    ),
                )
                component, active, _ = array_insert.setup(world=world, current=case["predicate"])
                active.graph.index = 0x01A3
                world.bind_script_component(123, component)
                flags = {123: case["source_flags"], 124: case["target_flags"]}
                world.array_filter_inputs = lambda entity, _flags=flags: (True, _flags[entity])
                world.set_entity_team_context(
                    123,
                    entity_team.Context(
                        123,
                        True,
                        True,
                        True,
                        lookup=lambda entity, _flags=flags: entity_team.Target(
                            entity, entity_team.FilterBits(_flags[entity])
                        ),
                    ),
                )
                query = {
                    "$": "STU_AC829876",
                    "m_entity": {
                        "$": "STUConfigVarDynamic",
                        "m_identifier": 2,
                        "m_60DB8F99": False,
                    },
                    "m_filter": case["filter"],
                }
                active.write((0, 2), expr.Entity(124))
                if "error" in expected:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, expected["error"]):
                        active.evaluate(query)
                else:
                    actual = active.evaluate(query)
                    self.assertIs(actual, expected["value"])

    def test_combat_target_contract(self):
        contract = _load_contract(
            "statescript-combat-target-v1.json",
            "statescript.combat-target.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld(mode=next(iter(scene.JUNKENSTEIN_MODES)))
                world.bind(123, target_tags=[])
                component, active, _ = array_insert.setup(world=world)
                active.graph.index = 0x0CCD
                world.bind_script_component(123, component)
                flags = {123: 8388609}
                for body in case["bodies"]:
                    entity = body["entity"]
                    world.bind(
                        entity,
                        target_tags=(
                            None
                            if body["tags"] is None
                            else [0x0D60000000000000 + tag for tag in body["tags"]]
                        ),
                    )
                    flags[entity] = body["flags"]
                    world.set_entity_team_context(entity, entity_team.Context(entity, True, True, True))
                if case["roster_available"]:
                    world.array_filter_inputs = lambda entity, _flags=flags: (True, _flags[entity])
                else:
                    world.set_entity_team_context(123, entity_team.Context(123, True, True, True))
                query = {
                    "$": "STU_B3E67A36",
                    "m_9C7BD8B8": {
                        "$": "STU_357DABBB",
                        "m_7D7C86A1": "0x0D60000000000001",
                        "m_resourceKey": None,
                    },
                    "m_filter": {"$": "STUConfigVarFilter", "m_84065FD1": 1, "m_4EA686DB": 16},
                }
                if "error" in expected:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, expected["error"]):
                        active.evaluate(query)
                else:
                    actual = active.evaluate(query)
                    self.assertEqual(actual, expr.Entity(expected["entity"]))

    def test_retired_entity_contract(self):
        contract = _load_contract(
            "statescript-retired-entity-v1.json",
            "statescript.retired-entity.v1",
        )
        cases, results = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(results))
        for case, expected in zip(cases, results, strict=True):
            self.assertEqual(case["name"], expected["name"])
            with self.subTest(case=case["name"]):
                world = scene.SceneWorld()
                world.possessors = set()
                owner, target = 123, case["entity"]
                world.bind(owner)
                world.bind(target)
                state = case["state"]
                if state in ("running", "faulted", "retired"):
                    component = runtime.Component(target, strict=True, world=world)
                    if "value" in case:
                        component.set(6604, case["value"])
                    world.bind_script_component(target, component)
                    if state == "faulted":
                        component.fault = RuntimeError("synthetic unavailable backend")
                elif state == "absent":
                    world.bind_script_component(target, None)
                if state == "retired":
                    world.unbind(target)
                if state != "unavailable":
                    world.set_entity_query_context(owner)
                    world.set_entity_team_context(
                        owner,
                        entity_team.Context(
                            owner,
                            True,
                            True,
                            True,
                            lookup=lambda identifier, _state=state, _case=case: (
                                None
                                if _state == "retired"
                                else entity_team.Target(
                                    identifier, entity_team.FilterBits(1 << (23 + _case["team"]))
                                )
                            ),
                        ),
                    )
                component, active, _ = array_insert.setup(world=world, current=expr.Entity(target))
                world.bind_script_component(owner, component)
                output = {}
                for key, config in contract["input"]["queries"].items():
                    try:
                        output[key] = active.evaluate(config)
                    except runtime.UnsupportedScriptError:
                        output[key] = "unavailable"
                self.assertEqual(output, expected["output"])

    def test_mover_velocity_read_contract(self):
        contract = _load_contract(
            "statescript-velocity-v1.json",
            "statescript.mover-velocity-read.v1",
        )
        cases, expected = contract["input"]["cases"], contract["expected"]["results"]
        self.assertEqual(len(cases), len(expected))
        self.assertEqual(len({case["name"] for case in cases}), len(cases))
        for case, result in zip(cases, expected, strict=True):
            self.assertEqual(case["name"], result["name"])
            with self.subTest(case=case["name"]):
                world = runtime.World() if case["table"] is None else scene.SceneWorld()
                for entity, velocity in case["table"] or ():
                    if velocity is None:
                        callback = None
                    else:
                        decoded = _decode_value(velocity)
                        callback = lambda v=decoded: v  # noqa: E731
                    world.bind(entity, velocity=callback)
                _, active, _ = array_insert.setup(world=world, current=_decode_value(case["target"]))
                config = {
                    "$": "STU_309B0570",
                    "m_EE729DCB": 0,
                    "m_entity": case.get(
                        "selector",
                        {"$": "STUConfigVarDynamic", "m_identifier": 1, "m_60DB8F99": False},
                    ),
                    "m_5B94EDB8": case.get("mover"),
                }
                if "error" in result:
                    with self.assertRaisesRegex(runtime.UnsupportedScriptError, result["error"]):
                        active.evaluate(config, initial=_decode_value(case["initial"]))
                else:
                    actual = active.evaluate(config, initial=_decode_value(case["initial"]))
                    self.assertEqual(
                        json.dumps(_encode_value(actual), sort_keys=True),
                        json.dumps(result["value"], sort_keys=True),
                    )
