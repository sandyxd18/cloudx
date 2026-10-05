from pydantic import BaseModel, Field

# ── Pydantic schemas ───────────────────────────────────────────────────────────

class VolumeIn(BaseModel):
    name:          str
    size_gb:       int | None = Field(None, gt=0)
    size:          int | None = None
    image:         str | None = None
    image_id:      str | None = None
    snapshot:      str | None = None
    source_volume: str | None = None
    bootable:      bool = False
    volume_type:   str | None = None
    multiattach:   bool = False


class VolumeInternalIn(BaseModel):
    name:             str
    size_gb:          int = Field(gt=0)
    image_id:         str | None = None
    image_ref:        str | None = None
    snapshot_id:      str | None = None
    source_volume_id: str | None = None
    bootable:         bool = False
    source_type:      str | None = None
    volume_type:      str | None = None
    project_id:       str | None = None


class AttachIn(BaseModel):
    server: str = ""
    server_id: str | None = None

    def model_post_init(self, __context):
        if not self.server and self.server_id:
            self.server = self.server_id


class SnapshotIn(BaseModel):
    name: str


class SnapshotScheduleIn(BaseModel):
    interval_seconds: int = Field(default=3600, ge=60)
    retention_count: int = Field(default=5, ge=1)


class InternalAttachIn(BaseModel):
    server_id:     str
    target_device: str | None = None


class VolumeTypeIn(BaseModel):
    name:           str
    description:    str | None = None
    backend:        str = "lvm"
    disk_type:      str = "sata_ssd"
    max_iops:       int | None = None
    max_throughput: int | None = None
    is_public:      bool = True
    lvm_type:       str = "default"   # default | thin


class ConsistencyGroupIn(BaseModel):
    name:        str
    description: str | None = None
    volume_ids:  list[str] = []


class CGSnapshotIn(BaseModel):
    name:                 str
    description:          str | None = None
    consistency_group_id: str


# ── Backup ─────────────────────────────────────────────────────────────────────

class BackupIn(BaseModel):
    name: str | None = None


class RestoreIn(BaseModel):
    name: str | None = None            # name for the restored volume
    volume_type: str | None = None


# ── Volume Quotas ─────────────────────────────────────────────────────────────

class QuotaIn(BaseModel):
    volumes:   int | None = None
    gigabytes: int | None = None
    snapshots: int | None = None


# ── Volume Migration ─────────────────────────────────────────────────────────
# Allows an operator to migrate a volume to a different backend/host.
# The volume must be in 'available' state (not attached) for a cold migration,
# or in 'in-use' state with live_migration=True for live migration.
# Reference: Block storage volume migration specification

class VolumeMigrateIn(BaseModel):
    host: str                        # target host (e.g. "block_store@lvm#lvm")
    force_host_copy: bool = False    # force copy even if driver-assisted migration is possible
    lock_volume: bool = False        # lock during migration (prevents use while migrating)


# ── Volume Replication & DR (Phase 7.3) ──────────────────────────────────────

class ReplicationEnableIn(BaseModel):
    target: str
    mode: str = "async"
