"""A variable is sent as its base plus its bindings: the client resolves the bound Stacks itself."""

import unittest
from types import SimpleNamespace

from ow174.game.script import expr, runtime
from ow174.game.script.driver import BodyScript, sent_value
from tests.runtime.test_game_graph_analysis import make_graph, node

SLOW = {"$": "STUConfigVarDynamic", "m_identifier": 23184, "m_60DB8F99": 1}


def adds(amount):
    """`#23184 + amount`, the form of every owner slow Stack in build 104319 (0D06 state 77 adds 0.5)."""
    return {
        "$": "STU_919FD47C", "m_EE729DCB": 0, "m_configVars": None,
        "m_expression": {
            "$": "STUConfigVarExpressionData", "m_fragments": None, "m_dynamicVars": [SLOW],
            "m_opcodes": [8, 0, 6, 0, 23, 0], "m_D99EF254": [amount],
        },
    }


def stack(position, value, *, server=False):
    return node(position, "STUStatescriptStateStack", state=position, server=server,
                fields={"m_out_Var": SLOW, "m_value": value})


def body(*nodes):
    component = runtime.Component(123)
    instance = component.create(make_graph(0xF002, nodes, states=range(len(nodes))))
    return component, instance


def sent(component):
    snapshot = SimpleNamespace(vars={})
    var = component.entity_var(23184)
    BodyScript._add_var(None, snapshot, 0, 23184, var)
    return var, snapshot.vars[(0, 23184)]


class StackSentValueTests(unittest.TestCase):
    def test_self_reading_stack_is_sent_over_the_value_below_it(self):
        component, instance = body(stack(0, adds(0.5)))
        instance.state(0).begin()
        var, (value, bindings) = sent(component)
        self.assertEqual(expr.to_float(var.value()), 0.5)
        # Before: 0.5 with the binding, and the client added its own 0.5 again (a full stop).
        self.assertIsNone(value)
        self.assertEqual([(item.instance, item.state) for item in bindings], [(instance.id, 0)])

    def test_two_self_reading_stacks_send_the_value_below_the_lowest(self):
        component, instance = body(stack(0, adds(0.5)), stack(1, adds(0.25)))
        instance.state(0).begin()
        instance.state(1).begin()
        var, (value, bindings) = sent(component)
        self.assertEqual(expr.to_float(var.value()), 0.75)
        self.assertIsNone(value)
        self.assertEqual(len(bindings), 2)

    def test_server_only_entry_below_reaches_the_client_through_the_value(self):
        component, instance = body(stack(0, adds(0.3), server=True), stack(1, adds(0.5)))
        instance.state(0).begin()
        instance.state(1).begin()
        var, (value, bindings) = sent(component)
        self.assertAlmostEqual(expr.to_float(var.value()), 0.8, places=6)
        self.assertAlmostEqual(expr.to_float(value), 0.3, places=6)
        self.assertEqual([item.state for item in bindings], [1])

    def test_constant_stack_is_left_to_the_client(self):
        component, instance = body(stack(0, {"$": "STUConfigVarFloat", "m_value": 0.4}))
        component.set(23184, 0.1)
        instance.state(0).begin()
        var, (value, bindings) = sent(component)
        self.assertAlmostEqual(expr.to_float(var.value()), 0.4, places=6)
        self.assertAlmostEqual(expr.to_float(value), 0.1, places=6)  # the client's copy replaces it
        self.assertEqual(len(bindings), 1)
        instance.state(0).end(False)
        self.assertAlmostEqual(expr.to_float(sent_value(var)), 0.1, places=6)

    def test_unbound_variable_sends_its_value(self):
        component, _ = body()
        component.set(23184, 0.2)
        _, (value, bindings) = sent(component)
        self.assertEqual(expr.to_float(value), 0.2)
        self.assertEqual(bindings, ())


if __name__ == "__main__":
    unittest.main()
