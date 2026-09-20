from fastmcp import FastMCP
from app.proxmox_ops import ProxmoxOps

mcp = FastMCP("Proxmox VE Operator")
ops: ProxmoxOps = None


def init_ops(pve_client):
    global ops
    ops = ProxmoxOps(pve_client)
    return ops


@mcp.tool()
def get_cluster_status() -> dict:
    """Retrieve overall Proxmox cluster health, node CPU load, memory utilization, and uptime."""
    return ops.get_cluster_status()


@mcp.tool()
def get_guest_inventory() -> dict:
    """List all virtual machines and LXC containers, including VMID, name, IP address, running state, CPU load, and allocated/used memory."""
    return ops.get_guest_inventory()


@mcp.tool()
def get_storage_health() -> dict:
    """Check storage pool utilization, total/used gigabytes, and status across all Proxmox pools."""
    return ops.get_storage_health()


@mcp.tool()
def get_backup_tasks(limit: int = 10) -> dict:
    """Retrieve recent vzdump backup job statuses and timestamps."""
    return ops.get_backup_tasks(limit=limit)


@mcp.tool()
def start_guest(vmid: int) -> dict:
    """Power on a stopped LXC container or VM by VMID (auto-detects node and guest type)."""
    return ops.start_guest(vmid)


@mcp.tool()
def stop_guest(vmid: int) -> dict:
    """Force stop a running LXC container or VM by VMID. Fails on protected infrastructure guests."""
    return ops.stop_guest(vmid)


@mcp.tool()
def reboot_guest(vmid: int) -> dict:
    """Reboot an LXC container or VM gracefully by VMID. Fails on protected infrastructure guests."""
    return ops.reboot_guest(vmid)


@mcp.tool()
def shutdown_guest(vmid: int) -> dict:
    """Gracefully shutdown an LXC container or VM by VMID. Fails on protected infrastructure guests."""
    return ops.shutdown_guest(vmid)


@mcp.tool()
def create_snapshot(vmid: int, snapname: str, description: str = "") -> dict:
    """Take a live disk snapshot of a container or VM before making changes (omits vmstate to prevent I/O freezes)."""
    return ops.snapshot_guest(vmid, snapname, description)


@mcp.tool()
def backup_guest(vmid: int, storage: str = None) -> dict:
    """Trigger an immediate vzdump backup of a container or VM to PBS or local storage."""
    return ops.backup_guest(vmid, storage=storage)


@mcp.tool()
def set_container_resources(vmid: int, memory_mb: int = None, swap_mb: int = None, cores: int = None) -> dict:
    """Adjust an LXC container's allocated memory (RAM in MB), swap (MB), or CPU core count live without reboot."""
    return ops.set_lxc_resources(vmid, memory_mb=memory_mb, swap_mb=swap_mb, cores=cores)


@mcp.tool()
def reboot_pve_hypervisor(confirm: bool = False) -> dict:
    """Reboot the physical/virtual Proxmox VE hypervisor host. Requires explicit confirm=True."""
    return ops.reboot_node(confirm=confirm)
