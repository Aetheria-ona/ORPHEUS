"""Public behavior contracts for player settings persistence (22200-22204 -> 20802)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ow174.accounts.profile import Profile, load_or_create_profile, save_profile
from ow174.content.player import PlayerMessages
from ow174.jam.groups import LOBBY, SOCIAL_OUT
from ow174.lobby.handlers.settings import routes

FIXTURES = Path(
    os.environ.get("ORPHEUS_BEHAVIOR_FIXTURES")
    or Path(__file__).resolve().parents[2] / "fixtures/behavior"
)


class _StubSchemas:
    def empty(self, group: int, msg_id: int) -> dict:
        assert (group, msg_id) == (LOBBY, 20802)
        return {
            "+0x78": {},
            "+0x108": {},
            "+0x10D": {},
            "+0x120": {},
            "+0x130": [],
        }


class _FakeSession:
    def __init__(self, profile: Profile, path: Path) -> None:
        self.profile = profile
        self._path = path
        self.save_calls = 0
        self.account = SimpleNamespace(account_lo=1)
        self.server = SimpleNamespace(social=SimpleNamespace(parties={}))

    def save(self) -> None:
        self.save_calls += 1
        save_profile(self.profile, self._path)

    def log(self, _message: str) -> None:
        pass


class ProfileSettingsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = json.loads(
            (FIXTURES / "profile-settings-v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["case_id"], "profile-settings.persistence.v1")
        self.assertEqual(self.contract["target_build"], 104319)
        self.assertEqual(self.contract["evidence"], "synthetic-reference")
        self.player = PlayerMessages(_StubSchemas(), None, None, None)

    def test_settings_cases_survive_save_and_reload(self) -> None:
        expected_cases = self.contract["expected"]["cases"]
        with tempfile.TemporaryDirectory() as tmp:
            for index, case in enumerate(self.contract["input"]["cases"]):
                name = case["name"]
                with self.subTest(case=name):
                    profile_path = Path(tmp) / f"profile_{index}.json"
                    profile = Profile.from_dict(case["initial_profile"])
                    save_profile(profile, profile_path)
                    session = _FakeSession(profile, profile_path)

                    for packet in case["input_packets"]:
                        handler = routes.get(SOCIAL_OUT, packet["msg_id"])
                        self.assertIsNotNone(handler)
                        handler(session, packet["payload"])

                    reloaded = load_or_create_profile(profile_path)
                    expected = expected_cases[name]
                    self.assertEqual(reloaded.settings, expected["settings"])
                    self.assertEqual(self.player.settings(reloaded), expected["message_20802"])

    def test_from_dict_rejects_out_of_range_and_non_integer_entries(self) -> None:
        invalid_keys = [-2147483649, 4294967296, True, "not-a-number", ""]
        for bad_key in invalid_keys:
            with self.subTest(bad_key=bad_key), self.assertRaises(ValueError):
                Profile.from_dict({"settings": {"+0x130": [{"+0x0": 1, "+0x8": bad_key}]}})

        invalid_values = [-(1 << 63) - 1, 1 << 64, False, "not-a-number", ""]
        for bad_val in invalid_values:
            with self.subTest(bad_val=bad_val), self.assertRaises(ValueError):
                Profile.from_dict({"settings": {"+0x130": [{"+0x0": bad_val, "+0x8": 1}]}})

        wrapped = Profile.from_dict({"settings": {"+0x130": [{"+0x0": 1 << 63, "+0x8": 1}]}})
        self.assertEqual(wrapped.settings["+0x130"], [{"+0x0": -(1 << 63), "+0x8": 1}])

    def test_invalid_22204_packet_does_not_mutate_or_save_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.json"
            profile = Profile.from_dict({"settings": {"+0x130": [{"+0x0": 5, "+0x8": 14725178}]}})
            save_profile(profile, profile_path)
            session = _FakeSession(profile, profile_path)
            handler = routes.get(SOCIAL_OUT, 22204)
            self.assertIsNotNone(handler)

            with self.assertRaises(ValueError):
                handler(session, {"+0x78": [{"+0x0": 99, "+0x8": 4294967296}]})

            self.assertEqual(session.save_calls, 0)
            self.assertEqual(profile.settings, {"+0x130": [{"+0x0": 5, "+0x8": 14725178}]})

    def test_load_skips_invalid_setting_entry_without_losing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "profile.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "player_name": "Keeper",
                        "level": 250,
                        "unlocks_reset_v2": True,
                        "settings": {
                            "+0x130": [
                                {"+0x0": 5, "+0x8": "0x00E0B03A"},
                                {"+0x0": 99, "+0x8": "bad-key"},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            reloaded = load_or_create_profile(profile_path)
            self.assertEqual(reloaded.player_name, "Keeper")
            self.assertEqual(reloaded.level, 250)
            self.assertEqual(reloaded.settings["+0x130"], [{"+0x0": 5, "+0x8": 14725178}])


if __name__ == "__main__":
    unittest.main()
