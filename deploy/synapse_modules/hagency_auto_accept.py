"""A Synapse module: a person's private chats from @pm and from their own agent open already joined,
so a newcomer sees @pm's "Get your agent" buttons (and their agent's chat) without finding and
accepting an invite first. Loaded by Synapse from /modules (configure_synapse.py).

Why not Synapse's built-in auto_accept_invites: it has no filter on who is invited or by whom. With
only_for_direct_messages and only_from_local_users, any local account could still pull anyone's
agent into a DM by inviting it (bridge.py only joins rooms its owner or @pm invites it to), and
@pm into DMs from agents and bots (pm_follow.py ignores those). So this accepts exactly two kinds
of invite, both marked is_direct, both to a person on this server:
  - from @pm (its private chat: the welcome offer, console links);
  - from that person's own agent, @<person>-agent (the chat it opens when it's set up).
Everything else stays an invite, for the person (or the bot's own code) to decide.

Like the built-in one, it marks the room as a direct chat in the person's account data (m.direct),
so Element lists it under People and its "message @pm" links reuse it.
"""
import logging

AGENT_SUFFIX = "-agent"                       # identity.AGENT_SUFFIX; Synapse can't import the app's code
BOT_SUFFIX = "-bot"
JOIN_TRIES = 3
log = logging.getLogger(__name__)


def _local(user_id: str, server: str) -> str | None:
    """The localpart of a user on `server`, else None."""
    if not isinstance(user_id, str) or not user_id.startswith("@") or not user_id.endswith(f":{server}"):
        return None
    local = user_id[1:-len(server) - 1]
    return local or None


def should_accept(event: dict, server: str, pm: str) -> bool:
    """An invite to a person here, marked direct, from @pm or from that person's own agent."""
    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    if (event.get("type") != "m.room.member" or "state_key" not in event
            or content.get("membership") != "invite" or content.get("is_direct") is not True):
        return False
    invitee, sender = event["state_key"], event.get("sender")
    person = _local(invitee, server)
    if person is None or invitee == pm or person.endswith((AGENT_SUFFIX, BOT_SUFFIX)):
        return False                          # people only: bots and agents keep deciding for themselves
    return sender == pm or sender == f"@{person}{AGENT_SUFFIX}:{server}"


def with_direct(direct, other: str, room_id: str) -> dict:
    """m.direct with `room_id` listed for `other` (once); other entries kept as they were."""
    out = {k: list(v) for k, v in (direct or {}).items() if isinstance(v, (list, tuple))}
    rooms = out.setdefault(other, [])
    if room_id not in rooms:
        rooms.append(room_id)
    return out


class AutoAccept:
    def __init__(self, config: dict, api) -> None:
        self.api, self.pm, self.server = api, config["pm"], config["server"]
        api.register_third_party_rules_callbacks(on_new_event=self.on_new_event)

    @staticmethod
    def parse_config(config: dict) -> dict:
        if (not isinstance(config, dict) or not str(config.get("pm", "")).startswith("@")
                or not config.get("server") or not str(config["pm"]).endswith(f":{config['server']}")):
            raise ValueError("hagency_auto_accept needs config: {pm: '@pm:<server>', server: '<server>'}")
        return config

    async def on_new_event(self, event, *args) -> None:
        if event.type != "m.room.member" or not should_accept(event.get_dict(), self.server, self.pm):
            return
        user = await self.api.get_userinfo_by_id(event.state_key)
        if user is None or user.is_deactivated or getattr(user, "suspended", False) or getattr(user, "locked", False):
            return                            # never for an account that can't use it
        # In the background, as the built-in does: joining from inside the invite's own callback can race it.
        self.api.run_as_background_process("hagency_auto_accept_join", self.join, event.state_key, event.room_id)
        direct = await self.api.account_data_manager.get_global(event.state_key, "m.direct")
        await self.api.account_data_manager.put_global(event.state_key, "m.direct",
                                                       with_direct(direct, event.sender, event.room_id))

    async def join(self, user_id: str, room_id: str) -> None:
        """Join as the person, a few tries; a failure leaves the invite for them to accept by hand."""
        error: Exception | None = None
        for attempt in range(JOIN_TRIES):
            await self.api.sleep(attempt)     # 0 s, 1 s, 2 s
            try:
                await self.api.update_room_membership(sender=user_id, target=user_id, room_id=room_id,
                                                      new_membership="join")
                return
            except Exception as e:  # noqa: BLE001 - Synapse's errors vary; retried, then logged below
                error = e
        # Not fatal: the invite is still there, shown in Element, for the person to accept by hand.
        log.warning("couldn't auto-join %s to %s: %s", user_id, room_id, error)
