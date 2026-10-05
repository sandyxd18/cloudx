from sqlalchemy import select

from .config import DEFAULT_SECURITY_GROUP_ALLOW_ICMP, DEFAULT_SECURITY_GROUP_ALLOW_SSH
from .db import SessionLocal
from .models import SecurityGroup, SecurityGroupRule, now
from .ovn_cli import apply_security_group_acls, ensure_security_group_port_group
from .serializers import get_by_ref


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

        def ensure_rule(direction: str, protocol: str, port_min: int | None = None, remote_ip_prefix: str | None = None):
            existing = db.scalar(
                select(SecurityGroupRule).where(
                    SecurityGroupRule.security_group_id == group.id,
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
                    protocol=protocol,
                    port_min=str(port_min) if port_min is not None else None,
                    port_max=str(port_min) if port_min is not None else None,
                    remote_ip_prefix=remote_ip_prefix,
                    action="allow",
                )
            )

        ensure_rule("egress", "any", remote_ip_prefix="0.0.0.0/0")
        if DEFAULT_SECURITY_GROUP_ALLOW_SSH:
            ensure_rule("ingress", "tcp", 22, "0.0.0.0/0")
        if DEFAULT_SECURITY_GROUP_ALLOW_ICMP:
            ensure_rule("ingress", "icmp", remote_ip_prefix="0.0.0.0/0")
        group.updated_at = now()
        db.commit()
        db.refresh(group)
        apply_security_group_acls(db, group)
    finally:
        db.close()
