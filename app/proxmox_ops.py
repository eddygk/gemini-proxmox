import re
from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException
from app.config import settings


class ProxmoxOps:
    def __init__(self, pve: ProxmoxAPI):
        self.pve = pve

    # --- Auto-Discovery Helper (Inspired by proxmox-ops/pve.sh) ---
    def _resolve_guest(self, vmid: int) -> tuple[str, str, str]:
        """Auto-discovers the hosting node, guest type ('lxc' or 'qemu'), and friendly name.
        Allows Gemini tools to operate seamlessly without requiring the user to specify node or type."""
        try:
            for res in self.pve.cluster.resources.get(type="vm"):
                if res.get("vmid") == vmid:
                    return res.get("node", "pve"), res.get("type", "lxc"), res.get("name", f"guest-{vmid}")
        except Exception:
            pass
        return "pve", "lxc", f"guest-{vmid}"

    # --- Telemetry & Status ---
    def get_cluster_status(self) -> dict:
        nodes = self.pve.nodes.get()
        metrics = []
        for n in nodes:
            mem_tot = n.get("maxmem", 0)
            mem_used = n.get("mem", 0)
            metrics.append({
                "node": n["node"],
                "status": n["status"],
                "uptime_hours": round(n.get("uptime", 0) / 3600, 1),
                "cpu_percent": round(n.get("cpu", 0) * 100, 1),
                "memory_used_gb": round(mem_used / (1024**3), 2),
                "memory_total_gb": round(mem_tot / (1024**3), 2),
                "memory_percent": round((mem_used / mem_tot) * 100, 1) if mem_tot else 0,
            })
        return {"node_count": len(nodes), "nodes": metrics}

    def get_guest_inventory(self) -> dict:
        guests = []
        for n in self.pve.nodes.get():
            node = n["node"]
            # QEMU Virtual Machines
            for vm in self.pve.nodes(node).qemu.get():
                mem_tot = vm.get("maxmem", 0)
                mem_used = vm.get("mem", 0)
                guests.append({
                    "id": vm["vmid"],
                    "name": vm.get("name", "unnamed"),
                    "node": node,
                    "type": "qemu",
                    "status": vm["status"],
                    "cpu_percent": round(vm.get("cpu", 0) * 100, 1),
                    "memory_used_mb": round(mem_used / (1024**2)),
                    "memory_total_mb": round(mem_tot / (1024**2)),
                    "ip": vm.get("tags", "").split(";")[0] if "10." in vm.get("tags", "") else None,
                })
            # LXC Containers
            for ct in self.pve.nodes(node).lxc.get():
                mem_tot = ct.get("maxmem", 0)
                mem_used = ct.get("mem", 0)
                tags = ct.get("tags", "")
                ip_match = re.search(r"\b(?:10|172|192)\.\d+\.\d+\.\d+\b", tags)
                guests.append({
                    "id": ct["vmid"],
                    "name": ct.get("name", "unnamed"),
                    "node": node,
                    "type": "lxc",
                    "status": ct["status"],
                    "cpu_percent": round(ct.get("cpu", 0) * 100, 1),
                    "memory_used_mb": round(mem_used / (1024**2)),
                    "memory_total_mb": round(mem_tot / (1024**2)),
                    "ip": ip_match.group(0) if ip_match else None,
                })
        return {"guest_count": len(guests), "guests": guests}

    def get_storage_health(self) -> dict:
        pools = []
        for n in self.pve.nodes.get():
            node = n["node"]
            for s in self.pve.nodes(node).storage.get():
                tot = s.get("total", 0)
                used = s.get("used", 0)
                pools.append({
                    "node": node,
                    "storage_id": s["storage"],
                    "type": s.get("type"),
                    "enabled": s.get("enabled", 0) == 1,
                    "active": s.get("active", 0) == 1,
                    "used_gb": round(used / (1024**3), 2),
                    "total_gb": round(tot / (1024**3), 2),
                    "percent_used": round((used / tot) * 100, 1) if tot else 0,
                })
        return {"storage_pools": pools}

    def get_backup_tasks(self, limit: int = 10) -> dict:
        tasks = []
        for n in self.pve.nodes.get():
            node = n["node"]
            for t in self.pve.nodes(node).tasks.get(typefilter="vzdump", limit=limit):
                tasks.append({
                    "node": node,
                    "vmid": t.get("id"),
                    "status": t.get("status", "unknown"),
                    "start_time": t.get("starttime"),
                    "end_time": t.get("endtime"),
                })
        return {"tasks": tasks}

    # --- Unified Guest Lifecycle Controls (LXC & QEMU) ---
    def start_guest(self, vmid: int) -> dict:
        try:
            node, gtype, name = self._resolve_guest(vmid)
            target = getattr(self.pve.nodes(node), gtype)(vmid)
            task = target.status.start.post()
            return {"status": "success", "action": "start", "vmid": vmid, "name": name, "type": gtype, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "start", "vmid": vmid, "error": str(e)}

    def stop_guest(self, vmid: int) -> dict:
        if vmid in settings.protected_vmids:
            return {"error": f"Rejected: Guest {vmid} is in protected_vmids (stopping it would disrupt critical infrastructure)."}
        try:
            node, gtype, name = self._resolve_guest(vmid)
            target = getattr(self.pve.nodes(node), gtype)(vmid)
            task = target.status.stop.post()
            return {"status": "success", "action": "stop", "vmid": vmid, "name": name, "type": gtype, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "stop", "vmid": vmid, "error": str(e)}

    def reboot_guest(self, vmid: int) -> dict:
        if vmid in settings.protected_vmids:
            return {"error": f"Rejected: Guest {vmid} is in protected_vmids (rebooting it would disrupt critical infrastructure)."}
        try:
            node, gtype, name = self._resolve_guest(vmid)
            target = getattr(self.pve.nodes(node), gtype)(vmid)
            task = target.status.reboot.post()
            return {"status": "success", "action": "reboot", "vmid": vmid, "name": name, "type": gtype, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "reboot", "vmid": vmid, "error": str(e)}

    def shutdown_guest(self, vmid: int) -> dict:
        if vmid in settings.protected_vmids:
            return {"error": f"Rejected: Guest {vmid} is in protected_vmids (shutting it down would disrupt critical infrastructure)."}
        try:
            node, gtype, name = self._resolve_guest(vmid)
            target = getattr(self.pve.nodes(node), gtype)(vmid)
            task = target.status.shutdown.post()
            return {"status": "success", "action": "shutdown", "vmid": vmid, "name": name, "type": gtype, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "shutdown", "vmid": vmid, "error": str(e)}

    def snapshot_guest(self, vmid: int, snapname: str, description: str = "") -> dict:
        try:
            node, gtype, name = self._resolve_guest(vmid)
            target = getattr(self.pve.nodes(node), gtype)(vmid)
            # Omit vmstate=1 per proxmox-ops standard to avoid severe I/O freezes
            task = target.snapshot.post(snapname=snapname, description=description)
            return {"status": "success", "action": "snapshot", "vmid": vmid, "name": name, "snapname": snapname, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "snapshot", "vmid": vmid, "error": str(e)}

    def backup_guest(self, vmid: int, storage: str = None, mode: str = "snapshot") -> dict:
        target_storage = storage or settings.proxmox_backup_storage
        try:
            node, gtype, name = self._resolve_guest(vmid)
            task = self.pve.nodes(node).vzdump.post(vmid=str(vmid), storage=target_storage, mode=mode)
            return {"status": "success", "action": "backup", "vmid": vmid, "name": name, "storage": target_storage, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "backup", "vmid": vmid, "error": str(e)}

    # --- Container Resource Adjustments (Live Hotplug) ---
    def set_lxc_resources(self, vmid: int, memory_mb: int = None, swap_mb: int = None, cores: int = None) -> dict:
        if vmid in settings.protected_vmids and (memory_mb is not None and memory_mb < 256):
            return {"error": f"Rejected: Cannot reduce protected CT {vmid} RAM below 256MB."}
        try:
            node, gtype, name = self._resolve_guest(vmid)
            if gtype != "lxc":
                return {"error": f"Resource resizing currently supported for LXC containers; guest {vmid} is {gtype}."}
            params = {}
            if memory_mb is not None:
                params["memory"] = memory_mb
            if swap_mb is not None:
                params["swap"] = swap_mb
            if cores is not None:
                params["cores"] = cores
            if not params:
                return {"error": "No resource parameters provided."}
            self.pve.nodes(node).lxc(vmid).config.put(**params)
            return {"status": "success", "vmid": vmid, "name": name, "applied": params}
        except ResourceException as e:
            return {"status": "error", "action": "set_resources", "vmid": vmid, "error": str(e)}

    # --- Hypervisor Node Control ---
    def reboot_node(self, node: str = "pve", confirm: bool = False) -> dict:
        if not confirm:
            return {"error": "Explicit confirmation required: confirm=True."}
        try:
            # Proxmox REST API: POST /nodes/{node}/status with command="reboot"
            task = self.pve.nodes(node).status.post(command="reboot")
            return {"status": "success", "action": "reboot_node", "node": node, "upid": task}
        except ResourceException as e:
            return {"status": "error", "action": "reboot_node", "node": node, "error": str(e)}
