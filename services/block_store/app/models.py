import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


# ── ORM models ─────────────────────────────────────────────────────────────────

DISK_TYPES = ("sata_ssd", "sas_ssd", "nvme_ssd", "sata_hdd", "sas_hdd")


class VolumeType(Base):
    """Performance profile for volumes — analogous to AWS EBS volume types."""
    __tablename__ = "block_store_volume_types"
    id:             Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:           Mapped[str]      = mapped_column(String, unique=True, index=True)
    backend:        Mapped[str]      = mapped_column(String, default="lvm")
    disk_type:      Mapped[str]      = mapped_column(String, default="sata_ssd")
    description:    Mapped[str|None] = mapped_column(String, nullable=True)
    max_iops:       Mapped[int|None] = mapped_column(Integer, nullable=True)
    max_throughput: Mapped[int|None] = mapped_column(Integer, nullable=True)
    is_public:      Mapped[bool]     = mapped_column(Boolean, default=True)
    lvm_type:       Mapped[str]      = mapped_column(String, default="default")  # default | thin
    created_at:     Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Volume(Base):
    __tablename__ = "block_store_volumes"

    id:                    Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:                  Mapped[str]      = mapped_column(String, unique=True, index=True)
    size_gb:               Mapped[int]      = mapped_column(Integer)
    status:                Mapped[str]      = mapped_column(String, default="creating")
    attached_to_server_id: Mapped[str|None] = mapped_column(String, nullable=True)
    path:                  Mapped[str]      = mapped_column(String, default="")
    backend:               Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    bootable:              Mapped[bool]     = mapped_column(Boolean, default=False)
    image_id:              Mapped[str|None] = mapped_column(String, nullable=True)
    snapshot_id:           Mapped[str|None] = mapped_column(String, nullable=True)
    source_volume_id:      Mapped[str|None] = mapped_column(String, nullable=True)
    source_type:           Mapped[str|None] = mapped_column(String, nullable=True)
    target_device:         Mapped[str|None] = mapped_column(String, nullable=True)
    project_id:            Mapped[str|None] = mapped_column(String, nullable=True, index=True)
    volume_type:           Mapped[str|None] = mapped_column(String, nullable=True)
    error_message:         Mapped[str|None] = mapped_column(String, nullable=True)
    multiattach:           Mapped[bool]     = mapped_column(Boolean, default=False)
    replication_status:    Mapped[str]      = mapped_column(String, default="disabled")
    replication_target:    Mapped[str|None] = mapped_column(String, nullable=True)
    replication_mode:      Mapped[str|None] = mapped_column(String, nullable=True, default="async")
    last_replicated_at:    Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    created_at:            Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Snapshot(Base):
    __tablename__ = "block_store_snapshots"

    id:         Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:       Mapped[str]      = mapped_column(String, unique=True, index=True)
    volume_id:  Mapped[str]      = mapped_column(String)
    size_gb:    Mapped[int]      = mapped_column(Integer)
    status:     Mapped[str]      = mapped_column(String, default="creating")
    path:       Mapped[str]      = mapped_column(String, default="")
    backend:    Mapped[str|None] = mapped_column(String, nullable=True, default="lvm")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VolumeSnapshotSchedule(Base):
    """Scheduled auto-snapshots for volumes."""
    __tablename__ = "block_store_snapshot_schedules"
    id:               Mapped[str]           = mapped_column(String, primary_key=True, default=new_id)
    volume_id:        Mapped[str]           = mapped_column(String, ForeignKey("block_store_volumes.id"))
    interval_seconds: Mapped[int]           = mapped_column(Integer, default=3600)
    retention_count:  Mapped[int]           = mapped_column(Integer, default=5)
    last_run_at:      Mapped[datetime|None] = mapped_column(DateTime, nullable=True)
    created_at:       Mapped[datetime]      = mapped_column(DateTime, default=datetime.utcnow)


class VolumeQuota(Base):
    """Per-project volume quota limits."""
    __tablename__ = "block_store_volume_quotas"
    project_id:  Mapped[str]     = mapped_column(String, primary_key=True)
    volumes:     Mapped[int]     = mapped_column(Integer, default=10)     # max number of volumes
    gigabytes:   Mapped[int]     = mapped_column(Integer, default=1000)   # max total GB
    snapshots:   Mapped[int]     = mapped_column(Integer, default=10)     # max snapshots
    created_at:  Mapped[datetime]= mapped_column(DateTime, default=datetime.utcnow)


class ConsistencyGroup(Base):
    """Group of volumes whose snapshots are taken atomically."""
    __tablename__ = "block_store_consistency_groups"
    id:          Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:        Mapped[str]      = mapped_column(String, unique=True, index=True)
    description: Mapped[str|None] = mapped_column(String, nullable=True)
    status:      Mapped[str]      = mapped_column(String, default="available")
    project_id:  Mapped[str|None] = mapped_column(String, nullable=True, index=True)
    volume_ids:  Mapped[str|None] = mapped_column(String, nullable=True)  # JSON list
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class VolumeBackup(Base):
    """Full-data volume backup stored as an object in MinIO/S3 (Swift-like)."""
    __tablename__ = "block_store_backups"
    id:            Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:          Mapped[str]      = mapped_column(String, index=True)
    volume_id:     Mapped[str]      = mapped_column(String, index=True)
    size_gb:       Mapped[int]      = mapped_column(Integer)
    status:        Mapped[str]      = mapped_column(String, default="creating")  # creating|available|restoring|error
    backend:       Mapped[str|None] = mapped_column(String, nullable=True)
    object_key:    Mapped[str|None] = mapped_column(String, nullable=True)   # MinIO object path
    project_id:    Mapped[str|None] = mapped_column(String, nullable=True, index=True)
    error_message: Mapped[str|None] = mapped_column(String, nullable=True)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CGSnapshot(Base):
    """Atomic snapshot of all volumes in a consistency group."""
    __tablename__ = "block_store_cgsnapshots"
    id:                    Mapped[str]      = mapped_column(String, primary_key=True, default=new_id)
    name:                  Mapped[str]      = mapped_column(String, unique=True, index=True)
    description:           Mapped[str|None] = mapped_column(String, nullable=True)
    consistency_group_id:  Mapped[str]      = mapped_column(String, index=True)
    status:                Mapped[str]      = mapped_column(String, default="creating")
    project_id:            Mapped[str|None] = mapped_column(String, nullable=True)
    created_at:            Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
