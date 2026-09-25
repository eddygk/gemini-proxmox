# Proxmox VE Universal MCP & Meta Muse Gateway

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Streamable%20HTTP-orange.svg)](https://modelcontextprotocol.io/)
[![Proxmox VE](https://img.shields.io/badge/Proxmox%20VE-8.x%20%7C%209.x-E57000.svg)](https://www.proxmox.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An enterprise-grade, defense-in-depth operations gateway exposing Proxmox VE hypervisor management to **Meta Muse**, **Google Gemini (Custom Connected Apps)**, and **any MCP client** supporting the **Model Context Protocol (MCP)** over **Streamable HTTP** with RFC OAuth 2.0 / Bearer authentication, while retaining authenticated local REST telemetry for internal monitoring and SRE automation pipelines.

---

## Architecture Overview

```mermaid
flowchart TD
    subgraph GoogleCloud["Google Cloud / Gemini Spark"]
        GeminiClient["Gemini Spark Core Engine\n(User Prompt: '@Proxmox Operator snapshot CT 102')"]
    end

    subgraph Edge["Cloudflare Edge Security"]
        WAF["WAF Custom Perimeter Rule\n• Allow: Google ASNs (AS15169, AS396982)\n• Allow: /oauth/authorize (Browser Consent)\n• Block: All other public traffic (HTTP 403)"]
        Tunnel["Cloudflare Zero Trust Tunnel\n(Encrypted QUIC)"]
    end

    subgraph SecureDMZ["Isolated DMZ / VPC (e.g. 10.0.0.0/24)"]
        CFTunnelGuest["Ingress Container (cf-tunnel)"]
        Gateway["CT / Host (gemini-proxmox)\n• FastAPI + FastMCP (Streamable HTTP)\n• OAuth 2.0 / OIDC Authorization Server\n• Capability Path: /<MCP_SECRET_PATH>/mcp\n• Blast Radius Guardrail: PROTECTED_VMIDS"]
        LocalAgent["Internal Monitoring & SRE Automation\n(Bearer Auth / Internal REST API)"]
    end

    subgraph Hypervisor["Proxmox VE Hypervisor"]
        PVEAPI["Proxmox REST API :8006\n• User: gemini-operator@pve\n• Token: operator (privsep=1)\n• Role: GeminiOps (Audit, Power, Snapshot, Backup)"]
        Guests["Virtual Machines & LXC Containers"]
    end

    GeminiClient -->|HTTPS POST| WAF
    WAF --> Tunnel
    Tunnel --> CFTunnelGuest
    CFTunnelGuest --> Gateway
    LocalAgent -->|Direct LAN| Gateway
    Gateway -->|Firewall Pinhole :8006| PVEAPI
    PVEAPI --> Guests
```

---

## Key Features

* **Native Model Context Protocol (MCP):** Implements the official MCP Streamable HTTP transport for stateful, low-latency AI tool execution.
* **Universal MCP Client Compatibility:** Built strictly on the open Model Context Protocol (MCP) Streamable HTTP specification. Works out of the box with **Google Gemini (Custom Connected Apps)** and **any MCP client or agent framework** supporting Streamable HTTP and OAuth 2.0 (Authorization Code + PKCE) or Bearer token authentication (including Claude Desktop, Cursor, LibreChat, OpenWebUI, or custom LangChain/LlamaIndex/CrewAI agents).
* **Built-in OAuth 2.0 & OIDC Authorization Server:** Full RFC-compliant OpenID Connect discovery (`/.well-known/openid-configuration`, `/.well-known/oauth-authorization-server`), browser consent redirect flow (`/oauth/authorize`), and token issuance with signed HMAC-SHA256 JWT `id_token`.
* **Dual-Format Body Parser:** Handles both standard `application/x-www-form-urlencoded` and Google JSON token exchange payloads without FastAPI stream consumption errors.
* **5-Layer Defense-in-Depth:**
  1. **Edge WAF Filtering:** Restricts ingress strictly to Google datacenters (AS15169 / AS396982) and browser authorization.
  2. **128-bit Cryptographic Capability Path:** The MCP endpoint is mounted at an unguessable UUID path.
  3. **OAuth 2.0 Bearer Authentication:** Enforces signed JWT verification on MCP endpoints.
  4. **Code-Level Blast-Radius Guardrails:** Hardcoded `PROTECTED_VMIDS` prevents AI from stopping or rebooting critical ingress containers.
  5. **Hypervisor RBAC Separation:** Dedicated `GeminiOps` Proxmox role strictly excludes VM/storage deletion privileges.
* **Backward-Compatible REST Endpoints:** Exposes `/telemetry/*` endpoints authenticated via static LAN Bearer token for local dashboards and agents.

---

## 12 Registered Operational Tools

| Tool Name | Type | Description |
|-----------|------|-------------|
| `pve_cluster_status` | Read | Checks cluster quorum, health, node count, and resource load. |
| `pve_nodes_list` | Read | Lists hypervisor nodes, online state, CPU/RAM usage, and uptime. |
| `pve_guests_list` | Read | Returns complete inventory of all VMs and LXCs with running status. |
| `pve_guest_status` | Read | Detailed status, memory, CPU, and disk usage for a specific VMID. |
| `pve_storage_list` | Read | Evaluates storage pools and allocated disk usage. |
| `pve_recent_tasks` | Read | Shows recent tasks (backups, snapshots, starts) with exit statuses. |
| `pve_guest_start` | Write | Starts a stopped VM or container. |
| `pve_guest_stop` | Write | Shuts down a guest (fails on `PROTECTED_VMIDS`). |
| `pve_guest_reboot` | Write | Reboots a guest (fails on `PROTECTED_VMIDS`). |
| `pve_guest_snapshot` | Write | Takes a live snapshot without vmstate freeze (`vmid`, `snapname`, `description`). |
| `pve_guest_rollback` | Write | Reverts a guest to a previous snapshot (fails on `PROTECTED_VMIDS`). |
| `pve_guest_backup` | Write | Initiates an immediate vzdump backup of a guest to specified storage. |

---

## Cloudflare Edge WAF Architecture (Free Plan Solution)

On Cloudflare's Free tier, **Bot Fight Mode** runs in an isolated pipeline prior to WAF Custom Rules and challenges automated server-to-server POST requests from cloud datacenters with an interactive JavaScript challenge ("Just a moment..."). Because automated OAuth clients cannot solve browser JS challenges, account linking fails.

### The Solution:
1. Turn **Bot Fight Mode OFF** under *Security > Bots*.
2. Add a targeted **WAF Custom Rule** under *Security > Security rules*:
   ```text
   (http.host eq "pve-mcp.example.com" and not ip.src.asnum in {15169 396982} and not http.request.uri.path contains "/oauth/authorize")
   ```
3. Set Action to **Block** (HTTP 403).

**Result:** Google datacenters (AS15169 / AS396982) connect seamlessly to token and MCP endpoints, human browsers can complete `/oauth/authorize`, and all untrusted internet traffic is blocked cold at the edge.

---

## Installation & Quickstart

### 1. Automated Proxmox VE RBAC Provisioning
Log in to your Proxmox VE shell as `root` and run our least-privilege setup script:
```bash
# Downloads and creates the MCPUniversalOps role and dedicated service token
curl -sSL https://raw.githubusercontent.com/eddygk/gemini-proxmox/main/scripts/setup-pve-user.sh | bash
```
*Creates a dedicated service user and API token with `VM.Audit`, `VM.PowerMgmt`, `VM.Snapshot`, and `VM.Backup` permissions while strictly omitting container/VM deletion privileges.*

### 2. Interactive Setup Wizard (Auto-Generates `.env`)
Clone the repository and run the interactive CLI wizard:
```bash
git clone https://github.com/eddygk/gemini-proxmox.git
cd gemini-proxmox
pip install -r requirements.txt

# Launch the interactive configuration wizard:
python -m app.setup
```
The wizard will:
1. Prompt for Proxmox credentials and test the connection with a live API pre-flight probe.
2. Dynamically discover cluster VMs and LXCs and prompt you to select `PROTECTED_VMIDS`.
3. Set your power action safety policy (`ALLOW_POWER_ACTIONS`).
4. Auto-generate cryptographically secure tokens (`GATEWAY_SECRET`, `OAUTH_CLIENT_SECRET`, and capability path).
5. Output your validated `.env` configuration file and display ready-to-paste Meta Muse and Gemini connection credentials.

### 3. Running with Docker Compose
```bash
docker compose up -d
```

### 4. Running with Systemd (LXC / Debian Host)
```bash
sudo useradd -r -s /usr/sbin/nologin gateway
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

sudo cp gemini-proxmox.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gemini-proxmox
```

---

## Connecting to AI Clients

### 1. Connecting to Meta Muse (Custom Connectors)

1. Open **Meta Muse** and navigate to **Settings > Connectors**.
2. Select **Add Custom Connector**.
3. **Connector Name:** `Proxmox VE Operator`
4. **Service URL / Endpoint:** `https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp`
5. **Authentication Method:** Select **OAuth 2.0 (Authorization Code)**.
   * **Client ID:** Matches `OAUTH_CLIENT_ID` in `.env`.
   * **Client Secret:** Matches `OAUTH_CLIENT_SECRET` in `.env`.
   * **Authorization Endpoint:** `https://pve-mcp.example.com/oauth/authorize`
   * **Token Endpoint:** `https://pve-mcp.example.com/oauth/token`
6. Complete the browser authorization prompt. Muse will establish connection, verify tools, and enable you to query hypervisor status and guests directly in chat!

### 2. Connecting to Google Gemini (Connected Apps)

1. Open **Google Gemini** -> **Connected Apps** -> **Add Custom App**.
2. **App Name:** `Proxmox VE Operator`
3. **App URL:** `https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp`
4. **Authentication:** Select **OAuth 2.0**.
   * **Client ID:** Matches `OAUTH_CLIENT_ID` in `.env`.
   * **Client Secret:** Matches `OAUTH_CLIENT_SECRET` in `.env`.
   * **Authorization URL:** `https://pve-mcp.example.com/oauth/authorize`
   * **Token URL:** `https://pve-mcp.example.com/oauth/token`
5. Click **Connect**. Gemini will authorize via browser redirect, exchange tokens, and immediately discover all 12 Proxmox tools.

### 3. Universal MCP Client Compatibility & Examples


Because this gateway implements the official **Model Context Protocol (MCP) Streamable HTTP** specification alongside RFC 6749 OAuth 2.0 / OpenID Connect, it functions as a universal hypervisor control plane for any compliant MCP client.

#### Supported Client Archetypes & Examples

| Client Category | Client Examples | Supported Authentication Methods |
|---|---|---|
| **Frontier Cloud Platforms** | **Google Gemini (Gemini Spark / Connected Apps)** | OAuth 2.0 (Authorization Code + PKCE, OIDC discovery) |
| **Developer IDEs & Coding Agents** | **Cursor, VS Code (Cline, Roo Code, Continue), Windsurf** | Streamable HTTP with Bearer Auth / Pre-shared Token |
| **Desktop & CLI Assistants** | **Claude Desktop, Claude Code** (via remote HTTP bridge) | Remote Streamable HTTP endpoint with Bearer header |
| **Open-Source AI Interfaces** | **LibreChat, OpenWebUI, Dify** | Remote MCP server with Bearer Token or OAuth 2.0 |
| **Autonomous Agent Frameworks** | **LangGraph, CrewAI, AutoGen, LlamaIndex, Python MCP SDK** | Native `streamable_http_client` with JWT / Bearer Auth |

#### Connection Specifications

* **Endpoint URL:** `https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp`
* **OAuth 2.0 Auto-Discovery:** Point your client or agent library to `https://pve-mcp.example.com/.well-known/oauth-authorization-server` or `/.well-known/openid-configuration`.
* **OAuth 2.0 Manual Setup:**
  * **Authorization URL:** `https://pve-mcp.example.com/oauth/authorize`
  * **Token URL:** `https://pve-mcp.example.com/oauth/token`
  * **Grant Types Supported:** `authorization_code` (with S256 PKCE), `refresh_token`, `client_credentials`.
  * **Client Auth Methods:** `client_secret_post` and `client_secret_basic`.
* **Static Bearer Token Auth:** For developer environments or agent frameworks that pass static tokens rather than interactive OAuth redirects, supply `Authorization: Bearer <API_BEARER_TOKEN>` or a valid JWT directly in the request headers.

#### Example 1: Developer IDE / Desktop Configuration (`mcpServers.json` / Cursor / Cline)

For clients that support direct HTTP MCP endpoints with custom headers:

```json
{
  "mcpServers": {
    "proxmox-operator": {
      "url": "https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR-JWT-OR-STATIC-TOKEN>"
      }
    }
  }
}
```

#### Example 2: LibreChat / OpenWebUI Remote MCP Integration

In enterprise chat interfaces supporting remote HTTP MCP servers, configure the tool gateway:
* **Server Name:** `Proxmox VE Operator`
* **Type:** `Streamable HTTP`
* **URL:** `https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp`
* **Auth Type:** `Bearer Token` or `OAuth 2.0 (Code Flow)`
* **Discovery Endpoint:** `https://pve-mcp.example.com/.well-known/oauth-authorization-server`

#### Example 3: Python MCP SDK Client Script

Connecting programmatically in Python using the official `mcp` SDK:

```python
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def main():
    headers = {"Authorization": "Bearer <YOUR-JWT-OR-STATIC-TOKEN>"}
    async with streamable_http_client(
        "https://pve-mcp.example.com/<MCP_SECRET_PATH>/mcp", 
        headers=headers
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Discovered {len(tools.tools)} Proxmox tools:")
            for tool in tools.tools:
                print(f" - {tool.name}: {tool.description}")

asyncio.run(main())
```

> [!NOTE]
> **Edge WAF Considerations for Non-Google Clients:**
> The Cloudflare WAF Custom Rule in this guide specifically allows Google ASNs (`AS15169`, `AS396982`). If you are connecting a client from another cloud provider (e.g. AWS, Anthropic, Azure) or a local IP, update your Cloudflare WAF rule to permit your client's source IP / ASN, or route traffic over a private LAN / WireGuard tunnel.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
