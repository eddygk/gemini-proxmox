# Proxmox VE Universal MCP & Meta Muse Gateway: Project Router

> **Repository:** `https://github.com/eddygk/gemini-proxmox` (public)  
> **Protocol:** Model Context Protocol (MCP) Streamable HTTP + OAuth 2.0 / OpenID Connect discovery  

Last modified: 2026-11-18 00:40 ET by Claude

This file is public and generic. The maintainer's own deployment notes (hosts, addresses, deploy history) are kept privately, outside this repo, and are loaded locally through an untracked `CLAUDE.local.md`. Never commit deployment-specific details (IPs, hostnames, domains, container IDs, key names) here or anywhere in this repo.

---

## 1. Quick Reference & Operational Commands

### Interactive Onboarding Wizard
To configure the gateway on a fresh node or generate a new `.env` file:
```bash
pip install -r requirements.txt
python -m app.setup
```
*Walks through host connection, runs a live `/api2/json` pre-flight probe, queries cluster guests to select `PROTECTED_VMIDS`, sets power policies, and auto-generates 64-char secrets.*

### Automated Proxmox VE RBAC Setup
Run on the Proxmox VE node shell as `root`:
```bash
bash scripts/setup-pve-user.sh
```
*Creates role `MCPUniversalOps` (`VM.Audit`, `VM.PowerMgmt`, `VM.Snapshot`, `VM.Backup`, `Datastore.AllocateSpace`), user `mcp-operator@pve`, and privilege-separated token `operator` (`privsep=1`).*

### Tests
```bash
pip install -r requirements.txt pytest
python -m pytest -q tests
python -m py_compile app/*.py
```

### Service Control (systemd install)
```bash
systemctl status gemini-proxmox
systemctl restart gemini-proxmox
journalctl -u gemini-proxmox -f
```

---

## 2. Architecture & Invariants

1. **Defense-in-Depth Power Safety (`ALLOW_POWER_ACTIONS`):**
   - By default, `ALLOW_POWER_ACTIONS=false`.
   - All mutating lifecycle calls (`start_guest`, `stop_guest`, `reboot_guest`, `shutdown_guest`, `reboot_node`) strictly reject requests at the execution layer unless explicitly enabled by the administrator.
2. **Blast-Radius Invariant (`PROTECTED_VMIDS`):**
   - Ingress and firewall containers (e.g. the container running your tunnel connector) must always be listed in `PROTECTED_VMIDS`.
   - The API wrapper rejects stop, reboot, shutdown, and rollback attempts targeting these IDs.
3. **Streamable HTTP Transport:**
   - Mounts the FastMCP sub-app at the unguessable 128-bit capability path: `/<MCP_SECRET_PATH>/mcp`.
   - OAuth 2.0 endpoints (`/oauth/authorize`, `/oauth/token`) support standard code flow + PKCE (S256) for Meta Muse and Google Gemini Connected Apps.
4. **Offline-node tolerance:**
   - Multi-node reads (`get_guest_inventory`, `get_storage_health`, `get_backup_tasks`) skip nodes whose status is not `online` and catch per-node errors, listing them in `offline_nodes`, so one powered-off node never fails a cluster-wide read.

---

## 3. Deployment Artifacts
- `Dockerfile`: Minimal Python 3.11-slim container running as unprivileged user `gateway`.
- `docker-compose.yml`: Standalone compose configuration with health check probe.
- `deploy.sh`: Script for syncing updates from PVE host staging into the gateway container.
- `gemini-proxmox.service`: systemd unit for a bare-metal or LXC install.
