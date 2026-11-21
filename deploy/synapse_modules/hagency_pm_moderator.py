"""A Synapse module: @pm is a moderator (power 50) in every new room made on this server, so it can
run the room (its mode, status board, side panel) without asking. Loaded by Synapse from
/modules (deploy/docker-compose.yml, configure_synapse.py; local_signup.py for the dev server).

Why a module and not default_power_level_content_override: that setting replaces the whole
"users" map, which drops a creator's 100 in older room versions and, in room version 12, puts a
creator in "users", which Synapse refuses, so every room @pm makes (and DMs to @pm) would fail.

Only room version 12 rooms (Synapse's default), where creators hold power by being creators and
"users" starts empty; @pm is left out when it is a creator itself (it made the room, or was
invited to a trusted private chat). Older versions are left alone: @pm asks for moderator there.
"""
V12 = ("12",)
TRUSTED = "trusted_private_chat"


def _ids(value) -> list:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def with_pm(config: dict, creator: str, pm: str, default_version: str = "12") -> bool:
    """Add @pm at 50 to a createRoom request (in place); True if it did. Any request shape it doesn't
    expect is left exactly as it came: Synapse validates it next."""
    version = str(config.get("room_version") or default_version)
    creation = config.get("creation_content") if isinstance(config.get("creation_content"), dict) else {}
    creators = {creator, *_ids(creation.get("additional_creators")),
                *(_ids(config.get("invite")) if config.get("preset") == TRUSTED else [])}
    override = config.get("power_level_content_override", {})
    initial = config.get("initial_state") if isinstance(config.get("initial_state"), list) else []
    if (version not in V12 or pm in creators or not isinstance(override, dict)
            or any(isinstance(e, dict) and e.get("type") == "m.room.power_levels" for e in initial)):
        return False                          # older rooms, @pm a creator, or the client sets the levels itself
    users = override.get("users")
    if users is not None and not isinstance(users, dict):
        return False
    if users and pm in users:                 # the creator chose a level for @pm: theirs to decide
        return False
    config["power_level_content_override"] = {**override, "users": {**(users or {}), pm: 50}}
    return True


class PmModerator:
    def __init__(self, config: dict, api) -> None:
        self.pm = config["pm"]
        self.version = str(config.get("default_room_version", "12"))
        api.register_third_party_rules_callbacks(on_create_room=self.on_create_room)

    @staticmethod
    def parse_config(config: dict) -> dict:
        if not isinstance(config, dict) or not str(config.get("pm", "")).startswith("@"):
            raise ValueError("hagency_pm_moderator needs config: {pm: '@pm:<server>'}")
        return config

    async def on_create_room(self, requester, config: dict, is_requester_admin: bool) -> None:
        with_pm(config, requester.user.to_string(), self.pm, self.version)
