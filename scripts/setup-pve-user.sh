#!/usr/bin/env bash
# ==============================================================================
# Setup Least-Privilege Proxmox VE Service User and API Token
# Run directly on your Proxmox VE node shell as root:
#   curl -sSL <raw-url>/setup-pve-user.sh | bash
#   or: bash scripts/setup-pve-user.sh
# ==============================================================================
set -euo pipefail

ROLE_NAME="MCPUniversalOps"
USER_NAME="mcp-operator"
REALM="pve"
FULL_USER="${USER_NAME}@${REALM}"
TOKEN_NAME="operator"

echo "======================================================================"
echo "  Proxmox VE Universal MCP & Meta Muse RBAC Provisioner"
echo "======================================================================"

# 1. Create custom role without container/storage allocation or deletion
if pveum role list | grep -qw "$ROLE_NAME"; then
    echo "[-] Role '$ROLE_NAME' already exists. Updating privileges..."
    pveum role modify "$ROLE_NAME" -privs "VM.Audit VM.PowerMgmt VM.Snapshot VM.Backup Datastore.AllocateSpace"
else
    echo "[+] Creating least-privilege role '$ROLE_NAME'..."
    pveum role add "$ROLE_NAME" -privs "VM.Audit VM.PowerMgmt VM.Snapshot VM.Backup Datastore.AllocateSpace"
fi

# 2. Create dedicated service user
if pveum user list | grep -qw "$FULL_USER"; then
    echo "[-] User '$FULL_USER' already exists."
else
    echo "[+] Creating dedicated service user '$FULL_USER'..."
    pveum user add "$FULL_USER" --comment "Universal MCP & Meta Muse Gateway Service User"
fi

# 3. Create API Token with privilege separation
echo "[+] Generating API token '$TOKEN_NAME' for '$FULL_USER'..."
# pveum user token add outputs token value once
TOKEN_OUTPUT=$(pveum user token add "$FULL_USER" "$TOKEN_NAME" --privsep 1 --output-format json-pretty 2>/dev/null || true)

if [ -z "$TOKEN_OUTPUT" ]; then
    echo "[!] Token may already exist or cannot be printed. Re-creating token..."
    pveum user token delete "$FULL_USER" "$TOKEN_NAME" 2>/dev/null || true
    TOKEN_OUTPUT=$(pveum user token add "$FULL_USER" "$TOKEN_NAME" --privsep 1 --output-format json-pretty)
fi

TOKEN_SECRET=$(echo "$TOKEN_OUTPUT" | grep -o '"value": "[^"]*' | cut -d'"' -f4)

# 4. Assign role across cluster paths
echo "[+] Binding '$ROLE_NAME' role to cluster paths..."
pveum acl modify /vms -user "$FULL_USER" -role "$ROLE_NAME"
pveum acl modify /nodes -user "$FULL_USER" -role "$ROLE_NAME"
pveum acl modify /storage -user "$FULL_USER" -role "$ROLE_NAME"

echo ""
echo "======================================================================"
echo "  Proxmox VE RBAC Setup Complete!"
echo "======================================================================"
echo "Proxmox User ID:         $FULL_USER"
echo "Proxmox Token Name:      $TOKEN_NAME"
echo "Proxmox Token Secret:    $TOKEN_SECRET"
echo ""
echo "Save these credentials securely! Pass them to the interactive setup wizard:"
echo "  python -m app.setup"
echo "======================================================================"
