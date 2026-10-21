"""Loader for packaging/manifest.toml, the single source of truth for CloudX units."""
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).resolve().parents[2] / "packaging" / "manifest.toml"

KINDS = ("api", "daemon", "unit")
FRONTENDS = ("nginx", "direct")
ROLES = ("controller", "compute", "storage")
FIELDS = {"name", "kind", "dir", "port", "workers", "frontend", "streaming", "catalog",
          "roles", "depends", "bundle_with"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    name: str
    kind: str
    dir: str | None
    port: int | None
    workers: int | None
    frontend: str | None
    streaming: bool
    catalog: str | None
    roles: tuple[str, ...]
    depends: tuple[str, ...]
    bundle_with: str | None

    @property
    def package(self) -> str:
        return f"cloudx-{self.name}"

    @property
    def unit_file(self) -> str:
        return f"cloudx-{self.name}.service"


def _unit(raw: dict) -> Unit:
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError(f"unit without a name: {raw}")
    unknown = set(raw) - FIELDS
    if unknown:
        raise ManifestError(f"{name}: unknown keys {sorted(unknown)}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ManifestError(f"{name}: kind must be one of {KINDS}, got {kind!r}")
    frontend = raw.get("frontend")
    if frontend is not None and frontend not in FRONTENDS:
        raise ManifestError(f"{name}: frontend must be one of {FRONTENDS}, got {frontend!r}")
    roles = tuple(raw.get("roles", ()))
    bad_roles = set(roles) - set(ROLES)
    if bad_roles:
        raise ManifestError(f"{name}: unknown roles {sorted(bad_roles)}")
    required = {"api": ("dir", "port", "workers", "frontend"), "daemon": ("dir",), "unit": ()}[kind]
    for key in required:
        if raw.get(key) is None:
            raise ManifestError(f"{name}: kind {kind} requires {key}")
    return Unit(
        name=name,
        kind=kind,
        dir=raw.get("dir"),
        port=raw.get("port"),
        workers=raw.get("workers"),
        frontend=frontend,
        streaming=bool(raw.get("streaming", False)),
        catalog=raw.get("catalog"),
        roles=roles,
        depends=tuple(raw.get("depends", ())),
        bundle_with=raw.get("bundle_with"),
    )


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[Unit]:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    units = [_unit(raw) for raw in data.get("unit", [])]
    names = [u.name for u in units]
    for name in sorted({n for n in names if names.count(n) > 1}):
        raise ManifestError(f"duplicate unit name {name}")
    for u in units:
        if u.bundle_with is not None and u.bundle_with not in names:
            raise ManifestError(f"{u.name}: bundle_with {u.bundle_with} is not a unit")
    return units
