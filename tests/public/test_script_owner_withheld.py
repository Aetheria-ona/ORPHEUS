"""Owner frames leave out Rocket Punch's STU_4773DA56 registration and its child."""

import unittest
from unittest.mock import patch

from ow174.game.script import driver, runtime
from tests.script_fixtures import graph, node, plug

PUNCH = 0x0D06


def punch_like(index=PUNCH):
    """A Stack-like parent (#22) over the registration (#138) and its child (#141), as in 0D06."""
    result = graph([
        node(22, "STU_TEST_PARENT", state=0, m_subgraphPlug=plug((138, "m_beginPlug"))),
        node(138, "STU_4773DA56", state=1, m_subgraphPlug=plug((141, "m_beginPlug"))),
        node(141, "STU_D09BD154", state=2),
    ])
    result.index = index
    return result


def sent_states(definition):
    component = runtime.Component(123)
    instance = component.create(definition, init=False)
    instance.initialized = True
    for index in range(3):
        instance.state(index).active = True
    snap = driver.Snapshot(0)
    with (
        patch.object(driver, "stec_classes", return_value=frozenset()),
        patch.object(driver, "sendable", return_value=True),
        patch.object(driver, "family", return_value="none"),
    ):
        for index, state in instance.states.items():
            driver.BodyScript._add_state(snap, instance.id, index, state)
    return {index for _, index in snap.states}


class OwnerWithheldTests(unittest.TestCase):
    def test_rocket_punch_registration_and_child_stay_off_on_the_owner(self):
        self.assertEqual(sent_states(punch_like()), {0})

    def test_the_same_classes_in_another_graph_are_still_sent(self):
        self.assertEqual(sent_states(punch_like(0x01C9)), {0, 1, 2})

    def test_withheld_entries_name_the_two_punch_nodes(self):
        self.assertEqual(driver.OWNER_WITHHELD, {(PUNCH, 138), (PUNCH, 141)})


if __name__ == "__main__":
    unittest.main()
