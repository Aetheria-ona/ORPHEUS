"""A player's saved progress: currencies, unlocks, loot boxes, loadouts and settings."""

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from ow174.catalog.boxes import box_name, starter_boxes

log = logging.getLogger(__name__)

DEFAULT_ICON = 0x02500000000002F7
# Profiles saved before this marker was written had their unlocks reset once.
UNLOCKS_RESET_MARKER = "unlocks_reset_v2"
_INT32_MIN = -(1 << 31)
_UINT32_MAX = (1 << 32) - 1
_INT32_SIGN_BIT = 1 << 31
_INT64_MIN = -(1 << 63)
_INT64_SIGN_BIT = 1 << 63
_UINT64_MAX = (1 << 64) - 1


def _parse_int_field(raw: object, field_name: str, minimum: int, maximum: int) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be an integer, got bool")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError(f"{field_name} must not be empty")
        base = 16 if text.lower().startswith(("0x", "-0x", "+0x")) else 10
        value = int(text, base)
    else:
        raise ValueError(f"{field_name} must be an integer or numeric string")
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} out of range [{minimum}, {maximum}]: {value}")
    return value


def normalize_setting_key(raw: object) -> int:
    """Parse a +0x130 compound key and normalize it to signed 32-bit int."""
    value = _parse_int_field(raw, "+0x8", _INT32_MIN, _UINT32_MAX)
    return value - (1 << 32) if value >= _INT32_SIGN_BIT else value


def normalize_setting_value(raw: object) -> int:
    """Parse a +0x130 value and normalize it to signed 64-bit int."""
    value = _parse_int_field(raw, "+0x0", _INT64_MIN, _UINT64_MAX)
    return value - (1 << 64) if value >= _INT64_SIGN_BIT else value


def normalize_setting_entries(entries: object) -> list[dict[str, int]]:
    """Canonicalize +0x130 entries into [{"+0x0": int, "+0x8": int}, ...]."""
    if not isinstance(entries, list):
        raise ValueError("settings['+0x130'] must be a list")
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict) or "+0x0" not in entry or "+0x8" not in entry:
            raise ValueError("each settings['+0x130'] entry must have '+0x0' and '+0x8'")
        normalized.append(
            {
                "+0x0": normalize_setting_value(entry["+0x0"]),
                "+0x8": normalize_setting_key(entry["+0x8"]),
            }
        )
    return normalized


def battle_tag(nickname: str, account_lo: int) -> str:
    """The player's BattleTag: the nickname and 4 digits from the account id ("Jeff#6691").

    The client shows the part before '#' as the name and the whole tag in the social screens, and
    players type it to add a friend. The digits never change.
    """
    return f"{nickname.strip()}#{1000 + account_lo % 9000}"


@dataclass
class Profile:
    player_name: str = "Researcher"
    region: str = "US"  # shop currency and country, see catalog/regions.py
    game_region: str = "americas"  # the game servers: americas, europe or asia
    level: int = 100
    credits: int = 50000
    comp_points: int = 6000
    league_tokens: int = 1000
    icon_guid: int = DEFAULT_ICON
    frame_guid: int = 0  # 0 means the frame follows the level
    next_box_id: int = 1
    loot_boxes: list = field(default_factory=list)  # [{"id", "type", "name"}]
    unlocked_items: list = field(default_factory=list)  # GUIDs as hex text, e.g. "0x0250..."
    unlock_all: bool = False
    bot_chat: bool = False  # the bot answers in chat, whispers and party
    friends: list = field(default_factory=list)  # login names of accepted friends
    friend_requests: list = field(default_factory=list)  # login names that asked to be friends
    last_online: int = 0  # unix time of the last login or logout; friends see "offline (2 h)"
    lobby_hero: str = "random"  # the menu hero: "random", "none", a hero name or a PvE character
    events: list = field(default_factory=lambda: ["goodbye"])  # active events, see catalog/events.py
    greeted_events: list = field(default_factory=list)  # celebrations whose start greeting was shown
    server_date: str = ""  # the clock sent to the client, e.g. "2022-10-03"; empty means now
    challenge: str = ""  # title of the active hero challenge
    challenge_wins: int = 0
    endorsement_level: int = 1
    ratings: dict = field(default_factory=dict)  # competitive rating per queue, see content/ranked.py
    season: int = 32  # the running competitive season, 1-32
    matches: dict = field(default_factory=dict)  # competitive matches played this season, per queue
    wins: dict = field(default_factory=dict)  # competitive matches won this season, per queue
    sms_protect: bool = True  # Battle.net SMS Protect, which Top 500 needs
    seasons_seen: list = field(default_factory=list)  # competitive cards whose season intro was shown
    priority_passes: dict = field(default_factory=dict)  # role queue pool -> passes, see content/passes.py
    loadouts: dict = field(default_factory=dict)  # hero GUID hex -> {slot key: unlock hex or list}
    settings: dict = field(default_factory=dict)  # settings the client saves (22200-22204)
    ux_states: dict = field(default_factory=dict)  # interface states from 22207, "index" -> value
    stats: dict = field(
        default_factory=lambda: {
            "boxes_opened": 0,
            "legendaries_dropped": 0,
            "credits_earned_from_duplicates": 0,
        }
    )

    def add_boxes(self, box_type: int, count: int) -> list[dict]:
        """Add loot boxes with ids never used before, even if next_box_id fell behind. Returns them."""
        highest_id = max((box["id"] for box in self.loot_boxes), default=0)
        first_id = max(self.next_box_id, highest_id + 1)
        added = [{"id": first_id + i, "type": box_type, "name": box_name(box_type)} for i in range(count)]
        self.loot_boxes += added
        self.next_box_id = first_id + count
        return added

    def unlocked_guids(self) -> set[int]:
        """The extra unlocks as integers; entries that are not numbers are ignored."""
        guids = set()
        for item in self.unlocked_items:
            try:
                guids.add(int(item, 0) if isinstance(item, str) else int(item))
            except ValueError:
                continue
        return guids

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        """Build a profile from saved JSON. Unknown keys are ignored, so old files still load."""
        known = {profile_field.name for profile_field in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        for key in ("icon_guid", "frame_guid"):  # saved as hex text
            if key in values:
                values[key] = int(str(values[key]), 0)
        if "settings" in values and values["settings"] is not None:
            if not isinstance(values["settings"], dict):
                raise ValueError("profile.settings must be a dict")
            settings = dict(values["settings"])
            if "+0x130" in settings and settings["+0x130"] is not None:
                settings["+0x130"] = normalize_setting_entries(settings["+0x130"])
            values["settings"] = settings
        return cls(**values)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["icon_guid"] = f"0x{self.icon_guid:016X}"
        data["frame_guid"] = f"0x{self.frame_guid:016X}"
        data[UNLOCKS_RESET_MARKER] = True
        return data


def save_profile(profile: Profile, path: Path) -> None:
    """Write the profile atomically, so a crash never leaves half a file."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def new_profile() -> Profile:
    boxes, next_id = starter_boxes()
    return Profile(next_box_id=next_id, loot_boxes=boxes, events=["goodbye", "halloween"])


def _sanitize_saved_settings(data: dict) -> None:
    settings = data.get("settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("+0x130"), list):
        return
    kept = []
    for entry in settings["+0x130"]:
        try:
            kept.extend(normalize_setting_entries([entry]))
        except ValueError as error:
            log.warning("Skipping invalid +0x130 setting entry: %s", error)
    settings["+0x130"] = kept


def _load_profile(path: Path) -> Profile:
    data = json.loads(path.read_text(encoding="utf-8"))
    _sanitize_saved_settings(data)
    profile = Profile.from_dict(data)
    if data.get(UNLOCKS_RESET_MARKER) is not True:
        profile.unlocked_items = []
        save_profile(profile, path)
    log.info(
        "Loaded profile '%s' from %s (level %d, boxes %d, credits %d, unlocks %d, events %s)",
        profile.player_name,
        path.name,
        profile.level,
        len(profile.loot_boxes),
        profile.credits,
        len(profile.unlocked_items),
        profile.events,
    )
    return profile


def load_or_create_profile(path: Path) -> Profile:
    """Load a profile, or create and save a fresh one when the file is missing or unreadable."""
    if path.is_file():
        try:
            return _load_profile(path)
        except (OSError, ValueError, TypeError) as error:
            log.warning("Could not read %s (%s), creating a new profile", path, error)
    profile = new_profile()
    save_profile(profile, path)
    log.info("Created a new profile with %d loot boxes in %s", len(profile.loot_boxes), path.name)
    return profile
