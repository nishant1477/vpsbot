import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=False)

BASE_DIR = Path(__file__).parent.resolve()

AGENT_VERSION = "2.0.1"

class AgentSettings:
    NODE_NAME: str = os.getenv("NODE_NAME", os.getenv("HOSTNAME", "node-1"))
    HOST: str = os.getenv("NODE_AGENT_HOST", "0.0.0.0")
    PORT: int = int(os.getenv("NODE_AGENT_PORT", "8080"))
    API_KEY: str = os.getenv("NODE_AGENT_API_KEY", "")
    SECRET_KEY: str = os.getenv("NODE_AGENT_SECRET", "arqon-node-secret-change-me")
    DEBUG: bool = os.getenv("NODE_AGENT_DEBUG", "false").lower() == "true"
    
    DOCKER_ENABLED: bool = os.getenv("DOCKER_ENABLED", "true").lower() == "true"
    LXC_ENABLED: bool = os.getenv("LXC_ENABLED", "true").lower() == "true"
    LXD_ENABLED: bool = os.getenv("LXD_ENABLED", "false").lower() == "true"
    KVM_ENABLED: bool = os.getenv("KVM_ENABLED", "false").lower() == "true"
    
    NETWORK_BRIDGE: str = os.getenv("NETWORK_BRIDGE", "arqonbr0")
    STORAGE_POOL: str = os.getenv("STORAGE_POOL", "default")
    LXD_STORAGE_POOL: str = os.getenv("LXD_STORAGE_POOL", "default")
    
    MAX_CONTAINERS: int = int(os.getenv("MAX_CONTAINERS", "100"))
    MAX_VMS: int = int(os.getenv("MAX_VMS", "10"))
    
    VPS_BASE_PATH: str = os.getenv("VPS_BASE_PATH", "/var/lib/arqon/vps")
    BACKUP_PATH: str = os.getenv("BACKUP_PATH", "/var/lib/arqon/backups")
    TEMPLATE_PATH: str = os.getenv("TEMPLATE_PATH", "/var/lib/arqon/templates")
    
    MONITOR_INTERVAL: int = int(os.getenv("MONITOR_INTERVAL", "30"))
    
    FIREWALL_ENABLED: bool = os.getenv("FIREWALL_ENABLED", "true").lower() == "true"
    FIREWALL_BACKEND: str = os.getenv("FIREWALL_BACKEND", "iptables")

agent_settings = AgentSettings()

def validate_settings() -> list[str]:
    errors = []
    if not agent_settings.API_KEY:
        errors.append("NODE_AGENT_API_KEY is required")
    return errors