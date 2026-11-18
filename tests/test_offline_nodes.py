"""Offline-node tolerance for multi-node telemetry reads (run: python -m pytest tests or python tests/test_offline_nodes.py)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PROXMOX_HOST", "x:8006")
os.environ.setdefault("PROXMOX_TOKEN_VALUE", "x")
from unittest.mock import MagicMock
from app.proxmox_ops import ProxmoxOps


def make(nodes, broken=()):
    pve = MagicMock()
    pve.nodes.get.return_value = nodes

    def node(name):
        if name in broken or name == "node1":
            raise Exception(f"hostname lookup '{name}' failed")  # any call on that node fails
        m = MagicMock()
        m.qemu.get.return_value = [{"vmid": 100, "name": "vm", "status": "running"}]
        m.lxc.get.return_value = [{"vmid": 200, "name": "ct", "status": "running"}]
        m.storage.get.return_value = [{"storage": "local", "total": 100, "used": 50, "active": 1, "enabled": 1}]
        m.tasks.get.return_value = [{"id": "200", "status": "OK"}]
        return m
    pve.nodes.side_effect = node
    return ProxmoxOps(pve)


NODES = [{"node": "node0", "status": "online"}, {"node": "node1", "status": "offline"}]


def test_offline_skipped():
    ops = make(NODES)
    g = ops.get_guest_inventory()
    assert g["guest_count"] == 2 and g["offline_nodes"][0]["node"] == "node1"
    assert ops.get_storage_health()["offline_nodes"][0]["node"] == "node1"
    assert len(ops.get_backup_tasks()["tasks"]) == 1


def test_online_but_erroring_node_skipped():
    ops = make([{"node": "node0", "status": "online"}, {"node": "node1", "status": "online"}])
    r = ops.get_guest_inventory()
    assert r["guest_count"] == 2 and "error" in r["offline_nodes"][0]["reason"]


if __name__ == "__main__":
    test_offline_skipped(); test_online_but_erroring_node_skipped(); print("OK")
