from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for Gemini-Proxmox MCP & REST Gateway."""

    # Public Hostname & Discovery
    public_base_url: str = "https://pve-mcp.example.com"

    # Proxmox Hypervisor
    proxmox_host: str = "127.0.0.1"
    proxmox_port: int = 8006
    proxmox_user: str = "gemini-operator@pve"
    proxmox_token_name: str = "operator"
    proxmox_token_value: str = ""
    proxmox_verify_ssl: bool = False
    proxmox_backup_storage: str = "local"

    # Security & Capability Path
    gateway_secret: str = ""
    mcp_secret_path: str = "mcp"

    # OAuth 2.0 Credentials (Gemini Connected Apps)
    oauth_client_id: str = "gemini-spark"
    oauth_client_secret: str = ""
    oauth_user_email: str | None = None

    # Blast-Radius Safeguards: Protect critical infrastructure guests from power actions
    protected_vmids: list[int] = []

    model_config = {
        "env_file": (".env", "/opt/gemini-proxmox/.env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
