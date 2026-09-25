import getpass
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

try:
    from proxmoxer import ProxmoxAPI
except ImportError:
    print("[!] 'proxmoxer' package not found. Run: pip install -r requirements.txt")
    sys.exit(1)


def prompt(text: str, default: str = "") -> str:
    default_hint = f" [{default}]" if default else ""
    val = input(f"{text}{default_hint}: ").strip()
    return val if val else default


def prompt_bool(text: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    val = input(f"{text} ({hint}): ").strip().lower()
    if not val:
        return default
    return val in ["y", "yes", "true", "1"]


def run_setup():
    print("=" * 72)
    print("  Proxmox VE Universal MCP & Meta Muse Gateway — Setup Wizard")
    print("=" * 72)
    print("This wizard will guide you through configuring your gateway, validating")
    print("your Proxmox VE connection, setting up safety guardrails, and generating")
    print("your production '.env' configuration file.\n")

    env_path = Path(".env")
    if env_path.exists():
        overwrite = prompt_bool("A .env file already exists. Overwrite it?", default=False)
        if not overwrite:
            print("Aborting setup. Existing .env was preserved.")
            return

    # 1. Proxmox VE API Configuration
    print("\n--- 1. Proxmox VE Hypervisor Connection ---")
    pve_host = prompt("Proxmox VE Host (IP or FQDN)", "192.168.1.100")
    pve_port_str = prompt("Proxmox VE API Port", "8006")
    try:
        pve_port = int(pve_port_str)
    except ValueError:
        pve_port = 8006

    pve_user = prompt("Proxmox User ID", "gemini-operator@pve")
    pve_token_name = prompt("Proxmox API Token Name", "operator")
    pve_token_value = getpass.getpass("Proxmox API Token Secret (hidden): ").strip()
    while not pve_token_value:
        print("Token secret cannot be empty.")
        pve_token_value = getpass.getpass("Proxmox API Token Secret (hidden): ").strip()

    pve_verify_ssl = prompt_bool("Verify Proxmox SSL certificate?", default=False)
    pve_backup_storage = prompt("Default Backup Storage Pool (e.g. local, pbs-datastore)", "local")

    # 2. Live Credential Validation Probe
    print("\n--- 2. Validating Proxmox VE API Credentials ---")
    print(f"Connecting to https://{pve_host}:{pve_port}/api2/json ...")
    pve = None
    discovered_guests = []
    try:
        pve = ProxmoxAPI(
            pve_host,
            port=pve_port,
            user=pve_user,
            token_name=pve_token_name,
            token_value=pve_token_value,
            verify_ssl=pve_verify_ssl,
            timeout=10,
        )
        version_info = pve.version.get()
        print(f"[✓] Connected successfully! Proxmox VE version: {version_info.get('release', 'unknown')} ({version_info.get('version', '')})")

        # Discover guests to assist with blast-radius configuration
        resources = pve.cluster.resources.get(type="vm")
        for res in resources:
            vmid = res.get("vmid")
            name = res.get("name", "unnamed")
            gtype = res.get("type", "guest")
            status = res.get("status", "unknown")
            discovered_guests.append((vmid, name, gtype, status))

        print(f"[✓] Discovered {len(discovered_guests)} guests in cluster.")
    except Exception as e:
        print(f"[!] Warning: Proxmox connection probe failed: {e}")
        proceed = prompt_bool("Would you like to save credentials anyway?", default=False)
        if not proceed:
            print("Setup aborted. Please verify your Proxmox credentials.")
            return

    # 3. Public Base URL & Capabilities
    print("\n--- 3. Public Hostname & Discovery ---")
    print("The public HTTPS URL routing to this gateway (e.g. via Cloudflare Tunnel or reverse proxy).")
    public_base_url = prompt("Public Base URL", "https://pve-mcp.example.com").rstrip("/")

    # 4. Blast-Radius Safeguards
    print("\n--- 4. Safety Guardrails & Blast-Radius Protection ---")
    print("Protected VMs/LXCs are strictly shielded: the AI cannot stop, reboot, or shut them down.")
    if discovered_guests:
        print("\nDiscovered cluster guests:")
        for vmid, name, gtype, status in discovered_guests:
            print(f"  • ID {vmid:<5} | {name:<20} | Type: {gtype:<4} | Status: {status}")
        print()

    suggested_protected = prompt(
        "Enter comma-separated VM/LXC IDs to shield (e.g. 100, 301)",
        default=""
    )
    protected_vmids = []
    if suggested_protected:
        for part in suggested_protected.split(","):
            part = part.strip()
            if part.isdigit():
                protected_vmids.append(int(part))

    print("\n--- 5. Mutating Power Action Policy ---")
    print("By default, the gateway operates in safe read-only/telemetry mode.")
    allow_power = prompt_bool(
        "Enable power actions (start, stop, reboot, shutdown) for AI agent?",
        default=False
    )

    # 6. Cryptographic Secret Generation
    print("\n--- 6. Cryptographic Secret Generation ---")
    gateway_secret = secrets.token_hex(32)
    mcp_secret_path = f"mcp-{uuid.uuid4().hex}"
    oauth_client_id = prompt("OAuth 2.0 Client ID (for Meta Muse / Gemini)", "proxmox-operator")
    oauth_client_secret = secrets.token_urlsafe(32)
    oauth_email = prompt("Operator Email (optional for OIDC identity)", "")

    # Generate .env content
    env_content = f"""# ==============================================================================
# Proxmox VE Universal MCP & Meta Muse Gateway — Environment Configuration
# Generated by app.setup interactive wizard
# ==============================================================================

# --- Public Hostname & Discovery ---
PUBLIC_BASE_URL={public_base_url}

# --- Proxmox VE Hypervisor API Credentials ---
PROXMOX_HOST={pve_host}
PROXMOX_PORT={pve_port}
PROXMOX_USER={pve_user}
PROXMOX_TOKEN_NAME={pve_token_name}
PROXMOX_TOKEN_VALUE={pve_token_value}
PROXMOX_VERIFY_SSL={str(pve_verify_ssl).lower()}
PROXMOX_BACKUP_STORAGE={pve_backup_storage}

# --- Secret Capability Path & Gateway Security ---
GATEWAY_SECRET={gateway_secret}
MCP_SECRET_PATH={mcp_secret_path}

# --- OAuth 2.0 Credentials (Meta Muse / Google Gemini Connected Apps) ---
OAUTH_CLIENT_ID={oauth_client_id}
OAUTH_CLIENT_SECRET={oauth_client_secret}
"""
    if oauth_email:
        env_content += f"OAUTH_USER_EMAIL={oauth_email}\n"

    env_content += f"""
# --- Blast-Radius Safeguards ---
# List of VM/LXC IDs shielded against stop, reboot, and shutdown actions
PROTECTED_VMIDS={json.dumps(protected_vmids)}

# Set to true to permit the AI agent to execute power actions (start, stop, reboot, shutdown)
ALLOW_POWER_ACTIONS={str(allow_power).lower()}
"""

    env_path.write_text(env_content, encoding="utf-8")
    print(f"\n[✓] Successfully generated and wrote {env_path.resolve()}!")

    # Print Client Connection Details
    print("\n" + "=" * 72)
    print("  Ready to Connect! Client Connection Details")
    print("=" * 72)
    print(f"• MCP Streamable HTTP Endpoint:")
    print(f"  {public_base_url}/{mcp_secret_path}/mcp\n")
    print(f"• OAuth 2.0 Authorization Endpoint:")
    print(f"  {public_base_url}/oauth/authorize\n")
    print(f"• OAuth 2.0 Token Endpoint:")
    print(f"  {public_base_url}/oauth/token\n")
    print(f"• OAuth Client ID:     {oauth_client_id}")
    print(f"• OAuth Client Secret: {oauth_client_secret}\n")
    print(f"• Internal LAN Bearer Token:")
    print(f"  {gateway_secret}\n")
    print("Next steps:")
    print("  1. Launch the server: uvicorn app.main:app --host 0.0.0.0 --port 8000")
    print("  2. If using Docker:   docker compose up -d")
    print("  3. Register the endpoint in Meta Muse (Settings > Connectors) or Gemini.")
    print("=" * 72 + "\n")


if __name__ == "__main__":
    run_setup()
