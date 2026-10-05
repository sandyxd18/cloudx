import logging
import os

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://cloudx:cloudx@127.0.0.1:5672/")
log = logging.getLogger("networking-api")

OVN_NB_DB = os.getenv("OVN_NB_DB", "tcp:127.0.0.1:6641")
PROVIDER_BRIDGE = os.getenv("PROVIDER_BRIDGE", "br-ex")
PROVIDER_PHYSNET = os.getenv("PROVIDER_PHYSNET", "physnet1")
PROVIDER_INTERFACE = os.getenv("PROVIDER_INTERFACE", "enp0s9")
INTERNAL_VIP_ADDRESS = os.getenv("INTERNAL_VIP_ADDRESS", "172.24.4.2/24")
# Host IP for the metadata route: only set when INTERNAL_VIP_ADDRESS is given explicitly (no default here).
# Strip CIDR notation if present (e.g. "192.168.10.102/24" → "192.168.10.102")
_vip_env = os.getenv("INTERNAL_VIP_ADDRESS", "")
PROVIDER_HOST_IP_RAW = _vip_env.split("/")[0].strip() if _vip_env else ""
METADATA_LINK_LOCAL = "169.254.169.254"
DEFAULT_SECURITY_GROUP_ALLOW_SSH = os.getenv("DEFAULT_SECURITY_GROUP_ALLOW_SSH", "true").lower() == "true"
DEFAULT_SECURITY_GROUP_ALLOW_ICMP = os.getenv("DEFAULT_SECURITY_GROUP_ALLOW_ICMP", "true").lower() == "true"
