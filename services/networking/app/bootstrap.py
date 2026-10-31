from sqlalchemy import select

from .config import DEFAULT_SECURITY_GROUP_ALLOW_ICMP, DEFAULT_SECURITY_GROUP_ALLOW_SSH
from .db import SessionLocal
from .models import Network, RouterInterface, SecurityGroup, SecurityGroupRule, Subnet, now
from .ovn_cli import apply_security_group_acls, ensure_router_port_ra, ensure_security_group_port_group
from .serializers import get_by_ref, network_mtu


def bootstrap_default_security_group() -> None:
    db = SessionLocal()
    try:
        group = get_by_ref(db, SecurityGroup, "default")
        if not group:
            group = SecurityGroup(name="default", description="Default lab security group", project_id="default")
            db.add(group)
            db.commit()
            db.refresh(group)
        ensure_security_group_port_group(group)

        def ensure_rule(direction: str, protocol: str, port_min: int | None = None, remote_ip_prefix: str | None = None,
                        ethertype: str = "IPv4"):
            existing = db.scalar(
                select(SecurityGroupRule).where(
                    SecurityGroupRule.security_group_id == group.id,
                    SecurityGroupRule.ethertype == ethertype,
                    SecurityGroupRule.direction == direction,
                    SecurityGroupRule.protocol == protocol,
                    SecurityGroupRule.port_min == (str(port_min) if port_min is not None else None),
                    SecurityGroupRule.remote_ip_prefix == remote_ip_prefix,
                )
            )
            if existing:
                return
            db.add(
                SecurityGroupRule(
                    security_group_id=group.id,
                    direction=direction,
                    ethertype=ethertype,
                    protocol=protocol,
                    port_min=str(port_min) if port_min is not None else None,
                    port_max=str(port_min) if port_min is not None else None,
                    remote_ip_prefix=remote_ip_prefix,
                    action="allow",
                )
            )

        # the same rules for both families; a restart adds the IPv6 ones to a default group made before they existed
        for ethertype, anywhere in (("IPv4", "0.0.0.0/0"), ("IPv6", "::/0")):
            ensure_rule("egress", "any", remote_ip_prefix=anywhere, ethertype=ethertype)
            if DEFAULT_SECURITY_GROUP_ALLOW_SSH:
                ensure_rule("ingress", "tcp", 22, anywhere, ethertype)
            if DEFAULT_SECURITY_GROUP_ALLOW_ICMP:
                ensure_rule("ingress", "icmp", remote_ip_prefix=anywhere, ethertype=ethertype)
        group.updated_at = now()
        db.commit()
        db.refresh(group)
        apply_security_group_acls(db, group)
    finally:
        db.close()


def bootstrap_router_advertisements() -> None:
    """Router ports on IPv6 subnets that were made before routers sent advertisements get them now. Setting the same
    values again at every start is harmless, and every networking-api instance does it."""
    db = SessionLocal()
    try:
        for iface, subnet in db.execute(select(RouterInterface, Subnet).join(Subnet, Subnet.id == RouterInterface.subnet_id).where(Subnet.ip_version == 6)).all():
            ensure_router_port_ra(iface.router_port_name, subnet, network_mtu(db.get(Network, subnet.network_id)))
    finally:
        db.close()
