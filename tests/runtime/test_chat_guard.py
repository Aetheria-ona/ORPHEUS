"""A failing match command answers one log line and never a traceback reply (the lobby channel)."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ow174.jam.groups import CHAT_IN
from ow174.lobby.handlers import chat


class FakeSession:
    local = False
    npc_menu_open = False

    def __init__(self):
        self.sent = []
        self.log_lines = []
        self.account = SimpleNamespace(account_lo=1, name="Alpha")
        self.profile = SimpleNamespace(bot_chat=False)
        self.server = SimpleNamespace(
            social=SimpleNamespace(
                bot=None,
                accounts=SimpleNamespace(bot=SimpleNamespace(name="BOT")),
                chat_message=lambda channel, sender, text, flag=0: {"sender": sender, "text": text},
                channel_members=lambda channel: [],
            ),
            game=None,
        )

    def log(self, line):
        self.log_lines.append(line)

    def send(self, group, pid, value):
        self.sent.append((group, pid, value))


class ChatGuardTests(unittest.TestCase):
    def value(self, text):
        return {"+0x78": {"+0x0": 7}, "+0x90": text}

    def test_a_failing_command_answers_no_line_and_dies_quietly(self):
        session = FakeSession()

        def broken(session_, channel, text):
            raise AttributeError("no attribute 'mode'")

        original, chat._match_command = chat._match_command, broken
        try:
            chat.chat(session, self.value(".body"))
        finally:
            chat._match_command = original
        self.assertTrue(any("AttributeError" in line for line in session.log_lines))
        self.assertEqual(session.sent, [])  # neither a traceback nor a broadcast went out

    def test_an_unknown_command_still_goes_out_as_chat(self):
        session = FakeSession()
        chat.chat(session, self.value(".nope"))
        # the try did not swallow the fall-through: the text took the broadcast path
        self.assertTrue(any("[chat" in line for line in session.log_lines))

    def test_a_long_reply_is_capped_at_one_chat_frame(self):
        session = FakeSession()
        chat._private_reply(session, {}, ".x", "y" * 400)
        group, pid, value = session.sent[0]
        self.assertEqual((group, pid), (CHAT_IN, 20400))
        self.assertLessEqual(len(value["text"]), 200)


if __name__ == "__main__":
    unittest.main()
