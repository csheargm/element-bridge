"""What a person means when they talk to @pm, in plain words. No AI here on purpose: "stop" must
work even when every model is down, and a fixed list of intents means a cleverly worded message
can't produce anything else. (An AI fallback for phrasings this misses can come later; it would
still only pick from this same list.)
"""
import re

OFF, ASSIST, AUTONOMOUS, STATUS, PLAN, HELP, UNKNOWN = (
    "off", "assist", "autonomous", "status", "plan", "help", "unknown")

_RULES = [   # checked in order; the first match wins, so the safest meanings come first
    (OFF, r"\b(stop|pause|halt|kill|freeze|turn (\w+ ){0,3}off|switch (\w+ ){0,3}off|shut (\w+ )?down|"
          r"hold (on|off)|cancel)\b"),
    (ASSIST, r"\b(approv\w*|check with (us|me)|ask (us|me)|assist\w*|let (us|me) (review|check|decide)|"
             r"review (each|every|it|them)|human review|before (you )?merg\w*|we merge|i('ll| will) merge)\b"),
    (AUTONOMOUS, r"\b(all the way|to the end|end to end|on your own|autonomous\w*|without asking|"
                 r"(just )?(get|do) it (all )?done|finish (it|the project|everything)|full auto|go all in)\b"),
    (STATUS, r"\b(status|where are we|progress|what'?s left|how'?s it going|what are (you|the agents) doing|"
             r"what'?s happening|update me)\b"),
    (PLAN, r"\b(let'?s (build|do|start|go|make)|build (it|this|that)|start (the project|building|it)|go ahead|"
           r"go for it|kick (it )?off|ship it|spec is ready|ready to build|make it|plan (it|this))\b"),
    (HELP, r"\b(help|what can you do|how do (i|we) use you)\b"),
]
_COMPILED = [(intent, re.compile(rx, re.I)) for intent, rx in _RULES]
YES_RE = re.compile(r"^\s*(yes|yep|yeah|confirm(ed)?|go|do it|ok(ay)?|sure|✅|👍)\W*\s*$", re.I)
CONFIRM_REACTIONS = ("✅", "👍", "✔️")


def classify(text: str) -> str:
    for intent, rx in _COMPILED:
        if rx.search(text):
            return intent
    return UNKNOWN


def is_yes(text: str) -> bool:
    return YES_RE.match(text) is not None
