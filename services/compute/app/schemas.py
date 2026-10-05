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
    volume_size: int | None = None    # root volume size override (GB)
    volume_type: str | None = None    # root volume type override (gp2, gp3, io1, sc1)


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


class AggregateActionIn(BaseModel):
    """Payload for add_host / remove_host actions."""
    host: str
