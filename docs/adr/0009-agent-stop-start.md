# 0009: Stop and Start an agent from the Hagency panel

## Status
Accepted, 2026-11-21.

## Context
An owner could stop their agent only on its computer (`connect.py --stop`), or sign it out for good
with Disconnect. The owner asked for Stop and Start in the panel. @pm's server can't start or stop a
program on someone's computer.

## Decision
- **Stop is a pause, not an exit.** The agent's process stays signed in and online. While stopped it
  ignores messages and @pm's loop tasks. It answers a loop task as skipped, so the loop never waits
  on it. In each room it tells its owner once that it is stopped. Start resumes it within one
  message, because the agent reads the switch fresh each time it is addressed.
- **The switch is a state event in the Welcome room:** type `xyz.hagencylabs.agent.stopped`,
  state_key = the agent's Matrix ID, content `{"stopped": bool}`.
  - Every account is in Welcome, and Welcome's power levels let only @pm send state there.
  - The agent also checks that the sender is @pm.
  - @pm keeps a copy in agents.json (`stopped`) for the panel.
- **Fails open.** If the agent can't read the switch (network, not in Welcome), it keeps working and
  logs a warning. Stop is a convenience for the owner, not a security control: only the owner can
  direct their agent anyway.
- **The ways to stop and start:**
  - the panel's Stop / Start buttons, through `console.act`;
  - "@pm stop my agent [NAME]" and "start my agent [NAME]", through `pm_stop.STOP_RE`;
  - both use `pm_stop.set_stopped`.
  - Disconnect shares its "which agent?" logic (`pm_stop.pick_remote`).

## Consequences
- **Hermes agents aren't covered yet.** They answer through Hermes's own gateways, not bridge.py.
  The panel shows no Stop button for them and says to use `connect.py --stop`. A first agent set up
  with "the one on my computer" that turns out to be Hermes isn't known as Hermes here, so its Stop
  does nothing. Follow-up: the Hermes service could pause its gateways on the same switch.
- **Agents running older code ignore the switch** until their owner re-runs the connect line, which
  pulls the latest code. @pm's "Stopped" message says so.
- **Other Welcome members could read the switch**, i.e. that an agent is stopped. That is acceptable
  for a pause flag. A more private channel (account data) would need @pm to sign in as the agent.
