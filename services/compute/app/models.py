import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import ONLY_BACKEND


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.utcnow()


class Server(Base):
    __tablename__ = "compute_servers"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    image_id: Mapped[str] = mapped_column(String)
    root_volume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    boot_source: Mapped[str | None] = mapped_column(String, nullable=True)
    bootable: Mapped[bool] = mapped_column(Boolean, default=True)
    flavor: Mapped[str] = mapped_column(String)
    flavor_name: Mapped[str | None] = mapped_column(String, nullable=True)
    vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    network_id: Mapped[str] = mapped_column(String)
    port_id: Mapped[str] = mapped_column(String)
    extra_port_ids: Mapped[str | None] = mapped_column(String, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String, nullable=True)
    user_data: Mapped[str | None] = mapped_column(String, nullable=True)
    vendor_data: Mapped[str | None] = mapped_column(String, nullable=True)
    keypair_name: Mapped[str | None] = mapped_column(String, nullable=True)
    public_key_snapshot: Mapped[str | None] = mapped_column(String, nullable=True)
    server_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
    backend: Mapped[str] = mapped_column(String, default=ONLY_BACKEND)
    host: Mapped[str | None] = mapped_column(String, nullable=True)
    instance_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    disk_path: Mapped[str | None] = mapped_column(String, nullable=True)
    config_drive_path: Mapped[str | None] = mapped_column(String, nullable=True)
    libvirt_domain_name: Mapped[str | None] = mapped_column(String, nullable=True)
    tap_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mac_address: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="BUILD")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    shelved_image_id: Mapped[str | None] = mapped_column(String, nullable=True)
    original_flavor: Mapped[str | None] = mapped_column(String, nullable=True)   # flavor id before resize
    original_vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_ram_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerTag(Base):
    __tablename__ = "compute_server_tags"
    server_id: Mapped[str] = mapped_column(String, primary_key=True)
    tag: Mapped[str] = mapped_column(String, primary_key=True)


class Flavor(Base):
    __tablename__ = "compute_flavors"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    vcpus: Mapped[int] = mapped_column(Integer)
    ram_mb: Mapped[int] = mapped_column(Integer)
    disk_gb: Mapped[int] = mapped_column(Integer)
    ephemeral_gb: Mapped[int] = mapped_column(Integer, default=0)
    swap_mb: Mapped[int] = mapped_column(Integer, default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_specs: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON object
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerAction(Base):
    """Instance action log (os-instance-actions): one row per action request."""
    __tablename__ = "compute_server_actions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    server_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, default=now)


class ComputeService(Base):
    """Operator state per compute host (os-services)."""
    __tablename__ = "compute_services"
    host: Mapped[str] = mapped_column(String, primary_key=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    forced_down: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ServerGroup(Base):
    __tablename__ = "compute_server_groups"
    id:         Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:       Mapped[str] = mapped_column(String, index=True)
    policy:     Mapped[str] = mapped_column(String, default="anti-affinity")  # anti-affinity | affinity | soft-anti-affinity
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    members:    Mapped[str | None] = mapped_column(String, nullable=True)  # comma-separated server IDs
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Keypair(Base):
    __tablename__ = "compute_keypairs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    public_key: Mapped[str] = mapped_column(String)
    fingerprint: Mapped[str] = mapped_column(String)
    key_type: Mapped[str] = mapped_column(String, default="rsa")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ── Host Aggregates ──────────────────────────────────────────────────────
# Host aggregates let operators group compute hosts and assign AZ metadata.
# Reference: Compute host aggregates specification

class Aggregate(Base):
    """Host aggregate definition."""
    __tablename__ = "compute_aggregates"
    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name:         Mapped[str] = mapped_column(String, unique=True, index=True)
    availability_zone: Mapped[str | None] = mapped_column(String, nullable=True)
    extra_metadata:     Mapped[str | None] = mapped_column(String, nullable=True)  # JSON string
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at:   Mapped[datetime] = mapped_column(DateTime, default=now)


class AggregateHost(Base):
    """Host membership in an aggregate."""
    __tablename__ = "compute_aggregate_hosts"
    id:           Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    aggregate_id: Mapped[str] = mapped_column(String, ForeignKey("compute_aggregates.id", ondelete="CASCADE"))
    host:         Mapped[str] = mapped_column(String)   # hostname / compute node name
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=now)
