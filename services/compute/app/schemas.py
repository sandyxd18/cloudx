from pydantic import BaseModel, Field


class ServerIn(BaseModel):
    name: str
    image: str | None = None
    boot_volume: str | None = None
    flavor: str
    network: str
    networks: list[str] = Field(default_factory=list)
    key_name: str | None = None
    keypair: str | None = None          # frontend alias for key_name
    backend: str | None = None
    user_data: str | None = None
    security_groups: list[str] = Field(default_factory=list)
    server_group: str | None = None   # server group id or name
    availability_zone: str | None = None
    volume_size: int | None = None    # root volume size override (GB)
    volume_type: str | None = None    # root volume type override (gp2, gp3, io1, sc1)
    ipv6: bool = False                # also allocate an IPv6 address on every network (refused if one has no IPv6 subnet)


class ServerResizeIn(BaseModel):
    flavor: str


class ServerRebuildIn(BaseModel):
    image: str


class ServerRescueIn(BaseModel):
    image: str | None = None


class ServerPasswordIn(BaseModel):
    password: str


class ServerRebootIn(BaseModel):
    type: str = "soft"  # soft | hard


class ServerGroupIn(BaseModel):
    name: str
    policy: str = "anti-affinity"


class ServerUserDataIn(BaseModel):
    user_data: str


class FlavorIn(BaseModel):
    name: str
    vcpus: int = Field(gt=0)
    ram_mb: int = Field(gt=0)
    disk_gb: int = Field(gt=0)
    ephemeral_gb: int = Field(default=0, ge=0)
    swap_mb: int = Field(default=0, ge=0)
    is_public: bool = True
    extra_specs: dict[str, str] = Field(default_factory=dict)


class ServiceUpdateIn(BaseModel):
    disabled: bool | None = None
    disabled_reason: str | None = None
    forced_down: bool | None = None


class MoveIn(BaseModel):
    host: str | None = None


class KeypairIn(BaseModel):
    name: str
    key_type: str = "rsa"  # rsa | ed25519


class KeypairImportIn(BaseModel):
    name: str
    public_key: str


class VolumeAttachIn(BaseModel):
    volume_id: str
    volume_name: str | None = None
    path: str
    host: str | None = None  # storage node that owns the LV; the compute-worker logs in over iSCSI if it is not itself
    target_dev: str | None = None


class ServerSnapshotIn(BaseModel):
    name: str


class ServerPortIn(BaseModel):
    port: str


class AggregateIn(BaseModel):
    name: str
    availability_zone: str | None = None


class AggregateUpdateIn(BaseModel):
    name: str | None = None
    availability_zone: str | None = None


class AggregateMetadataIn(BaseModel):
    metadata: dict[str, str | None]  # null value removes the key


class AggregateActionIn(BaseModel):
    """Payload for add_host / remove_host actions."""
    host: str
