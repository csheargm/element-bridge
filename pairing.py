"""One-time pairing codes: how a person's own computer gets their agent's sign-in, exactly once.

@pm gives the person a short code in their private chat; connect.py on their computer trades it
for the agent's credentials (remote_agent.py serves POST /connect). A code works once, for
CODE_S seconds, and only its hash is kept on disk. Wrong codes are rate-limited per client and in
total, so guessing is hopeless.
"""
import hashlib
import secrets
import time
from pathlib import Path

from saved_dict import SavedDict

CODE_S = 30 * 60
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L: easy to read out
CODE_LEN = 10                                  # ~49 bits
MAX_CODE_CHARS = 32
FAIL_WINDOW_S = 600
FAILS_PER_CLIENT = 5
FAILS_TOTAL = 50                               # behind a proxy every client may look the same


def normalize(code: str) -> str:
    return "".join(c for c in code.upper() if c.isalnum())


def digest(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()


class Codes:
    """hash(code) → {"person", "expires"}, on disk; never the code itself."""
    def __init__(self, path: Path) -> None:
        self.codes = SavedDict(path)

    def issue(self, person: str, now: float | None = None) -> str:
        """A new code for `person`; their older codes (and every expired one) stop working."""
        now = time.time() if now is None else now
        for h, entry in list(self.codes.items()):
            if not isinstance(entry, dict) or entry.get("person") == person or entry.get("expires", 0) <= now:
                self.codes.pop(h, None)
        raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        self.codes[digest(raw)] = {"person": person, "expires": now + CODE_S}
        return f"{raw[:5]}-{raw[5:]}"

    def redeem(self, code: str, now: float | None = None) -> str | None:
        """The person a fresh code was issued to, or None. The code is gone after this either way."""
        if not isinstance(code, str) or not code or len(code) > MAX_CODE_CHARS:
            return None
        entry = self.codes.pop(digest(code), None)
        now = time.time() if now is None else now
        if not isinstance(entry, dict) or entry.get("expires", 0) <= now:
            return None
        return entry.get("person") or None


class Limiter:
    """Failed attempts in the last FAIL_WINDOW_S: per client, and for everyone together."""
    def __init__(self) -> None:
        self.fails: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        kept = [t for t in self.fails.get(key, []) if t > now - FAIL_WINDOW_S]
        self.fails[key] = kept
        return kept

    def blocked(self, client: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return len(self._recent(client, now)) >= FAILS_PER_CLIENT or len(self._recent("*", now)) >= FAILS_TOTAL

    def failed(self, client: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for key in (client, "*"):
            self._recent(key, now).append(now)
