# Hagency Rooms on Oracle Cloud

The public server runs the shared parts. Each person's agent runs on that person's own computer
and connects to the server over Matrix, so model keys, GitHub accounts and tools never live on
the server.

```
Oracle VM (public)                         Each person's computer
──────────────────────────────────         ─────────────────────────────────────────
www.hagencylabs.xyz    the site            their agent's connector:
rooms.hagencylabs.xyz  Element Web           bridge (Claude Code / Codex) or Hermes
matrix.hagencylabs.xyz Synapse               → logs in as @anna-agent on the server
(later) @pm            the coordinator     their agent's GitHub sign-in
                                           Element desktop / mobile (or the web)
```

The existing `app.hagencylabs.xyz`, the apex redirect and this Mac's cloudflared tunnel stay as
they are.

## 1. You: create the VM (about 10 minutes)

In the OCI console (Compute → Instances → Create):
- **Image:** Canonical Ubuntu 24.04.
- **Shape:** Ampere A1 Flex, 4 OCPU / 24 GB (Always Free).
- **SSH key:** upload your own public key. Never paste a private key anywhere.
- **Networking:** in the subnet's security list, add ingress rules for TCP 80, 443 and 8448 from
  0.0.0.0/0. Port 22 is already open.

Then add the VM to `~/.ssh/config` on this Mac, using the instance's public IP:

```
Host hagency-vm
  HostName <public IP>
  User ubuntu
  IdentityFile ~/.ssh/<your key>
```

Check it with `ssh hagency-vm true`. When that works, tell Claude.

## 2. You: DNS in Cloudflare

Add three **A** records pointing at the VM's public IP, set to **DNS only** (grey cloud). Caddy
gets its own certificates, and Matrix federation on port 8448 can't pass Cloudflare's proxy.

| Name   | Type | Content       | Proxy    |
|--------|------|---------------|----------|
| www    | A    | `<public IP>` | DNS only |
| rooms  | A    | `<public IP>` | DNS only |
| matrix | A    | `<public IP>` | DNS only |

Don't change `hagencylabs.xyz` or `app`.

## 3. Claude: deploy

Claude does these steps once `ssh hagency-vm` works:

1. Copy `deploy/` and `site/` to `~/hagency` on the VM.
2. Create `~/hagency/.env` from `.env.example`:
   - your ACME e-mail;
   - image versions pinned to current releases, checked on the VM before use.
3. Run `bootstrap.sh`. It:
   - installs Docker from Ubuntu's archive;
   - opens 80/443/8448 in the VM's iptables (Oracle images block them even when the security
     list allows them);
   - generates a Postgres password and Synapse's config (`configure_synapse.py`: Postgres,
     invite-token sign-up only, no URL previews, admin API not exposed);
   - starts Caddy, Synapse, Postgres and Element.
4. Check the three sites, the federation tester and an invite-token sign-up.

Secrets stay in `~/hagency/.env` and `synapse/homeserver.yaml` on the VM (mode 600). They are
never printed, copied back or committed.

## 4. Next (separate steps)

- **@pm on the VM:** it reads GitHub with a read-only account of its own, never yours, and
  `HAGENCY_REPOS` lists the repos rooms may use.
- **The person's installer:** `curl -fsSL https://www.hagencylabs.xyz/install.sh | sh`. It installs
  their agent's connector (bridge or Hermes), signs the agent in to GitHub through the browser,
  registers `@<name>` and `@<name>-agent` with an invite token, and offers Element desktop.
- **Backups:** a nightly Postgres dump and `synapse/` media to Object Storage.
- **The apex:** once `hagencylabs.xyz` can serve `/.well-known/matrix/server`, new servers can use
  `hagencylabs.xyz` as their name. This one keeps its name for good.
