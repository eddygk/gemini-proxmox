#!/usr/bin/env bash
# Deploy Gemini Proxmox VE Operator into CT 680
# Run from PVE host: bash /tmp/deploy.sh
set -euo pipefail

CTID=${1:-680}
GW_DIR=${2:-/opt/gemini-proxmox}

echo "==> Ensuring service user exists..."
pct exec $CTID -- bash -c "id gateway >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin gateway"

echo "==> Creating project directory..."
pct exec $CTID -- mkdir -p $GW_DIR/app

echo "==> Copying application files..."
for f in requirements.txt app/__init__.py app/config.py app/security.py app/proxmox_ops.py app/mcp_server.py app/main.py gemini-proxmox.service; do
    pct push $CTID /tmp/gemini-staging/$f $GW_DIR/$f
done

echo "==> Creating virtualenv and installing deps..."
pct exec $CTID -- bash -c "rm -rf $GW_DIR/venv && python3 -m venv $GW_DIR/venv && $GW_DIR/venv/bin/pip install --upgrade pip && $GW_DIR/venv/bin/pip install -r $GW_DIR/requirements.txt"

echo "==> Installing systemd service..."
pct exec $CTID -- bash -c "cp $GW_DIR/gemini-proxmox.service /etc/systemd/system/ && systemctl daemon-reload"

echo "==> Setting ownership..."
pct exec $CTID -- bash -c "chown -R gateway:gateway $GW_DIR"

echo "==> Deployment complete."
