# Hagency Rooms on Oracle Cloud

The public server runs the shared side. Each person's agent runs on that person's own computer
and connects to the server over Matrix, so model keys, GitHub accounts and tools never live on
the server. The site is not on the VM: www.hagencylabs.xyz is GitHub Pages
(`.github/workflows/pages.yml` publishes `site/`).

```
Oracle VM (public, Always Free)                Each person's computer
───────────────────────────────────────        ─────────────────────────────────────────
rooms.hagencylabs.xyz   Element Web            their agent's connector (connect.py):
  /panel /console /auth/*  @pm's console         bridge running Codex or Claude Code
  /connect /connect.sh     pairing + installer   → logs in as @anna-agent on the server
matrix.hagencylabs.xyz  Synapse (443 + 8448)   Element desktop / mobile (or the web)
@pm                     welcome, pairing, the console (no model: PM_BRAIN=off)
```

Not on the VM: people's agents, the house agent (not built yet), any GitHub sign-in. The
existing `app.hagencylabs.xyz`, the apex redirect and this Mac's cloudflared tunnel stay as they are.

## What runs (deploy/docker-compose.yml)

| Service  | What | Reachable |
|----------|------|-----------|
| caddy    | HTTPS (Let's Encrypt), routes below | 80, 443, 8448 (the only published ports) |
| element  | Element Web, pointed at this homeserver | via Caddy |
| synapse  | the homeserver: invite-token sign-up only, no guests, no URL previews | via Caddy; `/_synapse/admin` is never routed |
| postgres | Synapse's database | an internal network only Synapse is on |
| pm       | @pm (`loop.py`): welcomes newcomers, the console, `/connect` and `/connect.sh` | via Caddy |

Images are pinned in the compose file, so a commit always runs the same versions. @pm runs as
uid 1000 from `~/hagency/pm` (that commit's files plus its own state: `.env` with its passwords,
`agents.json`, `data/`). It reads Synapse's registration secret from a copy bootstrap makes
(`data/registration_shared_secret`, mode 600), never Synapse's whole config.

## Free-tier guards

- Synapse: uploads up to 20 MB; local media kept 90 days, remote media 30 days (`media_retention`).
- Docker logs rotate (json-file, 3 × 10 MB per service); the journal is capped at 200 MB.
- `hagency-guard.timer` (daily, `deploy/guard.py`): @pm warns the operator (`OPERATOR` in
  `deploy/.env`) when the disk is over 80% or this month's outbound traffic is over 1 TB (vnstat).
  Over 5 TB, media downloads are paused for the rest of the month (`deploy/flags/media-paused`,
  which Caddy checks) and come back by themselves next month. `journalctl -u hagency-guard` shows
  every run.
- Nothing creates Oracle resources; the boot disk stays as it is.

## Once: the VM and DNS (the owner)

1. **VM:** Ubuntu 24.04, Ampere A1 Flex 4 OCPU / 24 GB. Ingress rules for TCP 80, 443 and 8448.
   `ssh hagency-vm` in `~/.ssh/config` on the Mac.
2. **DNS** in Cloudflare, **DNS only** (grey cloud): Caddy gets its own certificates, and
   federation on 8448 can't pass Cloudflare's proxy.

   | Name   | Type | Content       | Proxy    |
   |--------|------|---------------|----------|
   | rooms  | A    | `<public IP>` | DNS only |
   | matrix | A    | `<public IP>` | DNS only |

   `www` points at GitHub Pages (see the Pages settings), not at the VM.

Oracle's host iptables reject inbound ports other than 22, but Docker's published ports go through
Docker's own FORWARD rules, so nothing on the host firewall is changed.

## Deploy (from the Mac, from GitHub only)

```
deploy/deploy.sh            # the tip of master
deploy/deploy.sh <commit>   # any commit that is on master; anything else is refused
```

It checks the commit is on GitHub's master, then on the VM: fetches it from GitHub into
`~/hagency/app` (refusing if code there was edited), stops @pm, copies the commit's files to
`~/hagency/pm`, and runs `deploy/bootstrap.sh`, which:
- installs Docker, Compose and vnstat from Ubuntu's archive, caps the journal;
- creates `deploy/.env` once (mode 600, a random Postgres password; plain data, never sourced);
- generates Synapse's config once and adjusts it (`configure_synapse.py`: Postgres, token-only
  sign-up, the Welcome room @pm owns, media retention), atomically, refusing a changed server name;
- starts everything, reloads Caddy, installs the daily guard;
- checks inside Docker (`deploy/health.py --internal`): Element, Synapse, token-only sign-up,
  @pm's account and @pm's console.

Then `deploy.sh` checks over HTTPS from the Mac (`deploy/health.py`): Element, its config, the
client API, token-only sign-up, @pm serving `/connect.sh`, `.well-known`, federation on 8448, and
that the admin API isn't public. Certificates come on the first request once DNS points at the VM.

Secrets (`deploy/.env`, `deploy/synapse/homeserver.yaml`, `~/hagency/pm/.env`) are made on the VM,
mode 600, and are never printed, copied back or committed. Nobody edits code on the server:
change it here, push to master, deploy the commit.

## Invites and the operator

```
ssh hagency-vm ~/hagency/app/deploy/invite.sh token 5   # a token for 5 sign-ups, valid 7 days
ssh -t hagency-vm ~/hagency/app/deploy/invite.sh admin  # your own admin account (asks for a password)
```

`token` runs in @pm's container: it signs in as `admin-bot` (a server admin @pm made with the
registration secret; its password stays in `~/hagency/pm/.env`) only for Synapse's
registration-token API, and prints the token. People sign up at
`https://rooms.hagencylabs.xyz/#/register` with it, land in Welcome, and @pm offers them
🔌 Bring your own. To get the guard's warnings, put your account's name in `OPERATOR=` in
`~/hagency/app/deploy/.env` and deploy again.

## Next (separate steps)

- **The house agent** (🏠): run by the server with its own model key and a daily budget.
- **@pm's GitHub:** a read-only account of its own, and `HAGENCY_REPOS` for the repos rooms may use.
- **Backups:** a nightly Postgres dump and `synapse/` media, within the free Object Storage.
- **The apex:** once `hagencylabs.xyz` can serve `/.well-known/matrix/server`, new servers can use
  `hagencylabs.xyz` as their name. This one keeps its name for good.
