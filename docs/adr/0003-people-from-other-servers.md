# ADR 0003: A person is their full Matrix ID; people from other servers get an agent here

Date: 2026-11-20 · Status: accepted

## Context
Ownership was keyed by localpart: agents.json had "anna", the bridge obeyed `@anna:<this server>`,
and every check refused people from other servers, because a same-named `@anna:evil.org` would
otherwise have matched our anna (GPT reviews 11, 17, 21). That also kept out everyone on
matrix.org and other servers, though federation works.

## Decision
- **A person is their full Matrix ID** wherever ownership is decided: agents.json keys, the
  bridge's owner, @pm's agent-of and owner-of checks, follow-me and grants, pairing codes and
  /connect. Old files keyed by bare names are read as `@name:<this server>` (registry.load).
- **Every agent account lives on this server**, named from its person's full ID (identity.py):
  `anna-agent` for a plain name here; for anyone else a cleaned prefix plus 10 hex characters of
  the full ID's SHA-256, e.g. `alice-at-matrix-org-a5829e99c7-agent`. Plain names have no `-`, so
  the two kinds never meet; a name already owned by someone else, or an account that already
  exists without our password, is refused rather than shared.
- **People from other servers start with a private chat** with @pm (or "@pm I want an agent" in
  any room @pm is in): they never registered here, so there is no Welcome for them. @pm joins
  their chat, sends the offer in a chat it makes itself (theirs may be encrypted, which @pm
  can't read) and says where to find it.
- **Limits** (remote_people.py): `HAGENCY_ALLOW_REMOTE=0` turns it off; one agent per full ID; at
  most `HAGENCY_REMOTE_PER_HOUR` (10) new ones an hour for everyone together; @pm joins at most 30
  private chats from other servers an hour.

## Consequences
- The console's in-Element sign-in still checks the token with our homeserver, which only knows
  its own people. People from other servers use "@pm send me my console link". Verifying their
  server's token means fetching a URL that server names (discovery, and a guard against reaching
  internal addresses): deferred.
- Custom code versions (customize.py) stay for agents this server runs, i.e. people here.
- An attacker can't take over someone's agent by choosing a similar name, but could register the
  derived account name on this server first and block that person's agent (they'd be told the
  name is taken). Sign-up here needs an invite token, which limits that.
