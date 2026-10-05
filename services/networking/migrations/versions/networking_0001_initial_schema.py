"""initial schema for networking

Revision ID: networking_0001
Revises:
Create Date: 2026-10-05

Idempotent baseline. On a fresh database it creates every table. On a database built by an older release (when the
service still ran create_all()/ensure_columns() itself) it creates what is missing, adds missing columns and runs the old data fix-ups,
so `cloudx-db-migrate` is safe to run against any existing install.

The table definitions are frozen here on purpose: never edit them, add a new revision instead.
"""
from contextlib import contextmanager
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text
from shared.schema import add_missing_columns

revision = "networking_0001"
down_revision = None
branch_labels = None
depends_on = None

metadata = sa.MetaData()

sa.Table(
    "networking_ike_policies", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("auth_algorithm", sa.String(32), nullable=False),
    sa.Column("encryption_algorithm", sa.String(32), nullable=False),
    sa.Column("phase1_negotiation_mode", sa.String(32), nullable=False),
    sa.Column("pfs", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_ike_policies_name', 'name'),
    sa.Index('ix_networking_ike_policies_project_id', 'project_id'),
)

sa.Table(
    "networking_ipsec_policies", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("auth_algorithm", sa.String(32), nullable=False),
    sa.Column("encryption_algorithm", sa.String(32), nullable=False),
    sa.Column("pfs", sa.String(32), nullable=False),
    sa.Column("transform_protocol", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_ipsec_policies_name', 'name'),
    sa.Index('ix_networking_ipsec_policies_project_id', 'project_id'),
)

sa.Table(
    "networking_networks", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("external", sa.Boolean(), nullable=False),
    sa.Column("shared", sa.Boolean(), nullable=False),
    sa.Column("provider", sa.Boolean(), nullable=False),
    sa.Column("network_type", sa.String()),
    sa.Column("physical_network", sa.String()),
    sa.Column("bridge_mapping", sa.String()),
    sa.Column("provider_bridge", sa.String()),
    sa.Column("provider_cidr", sa.String()),
    sa.Column("gateway_ip", sa.String()),
    sa.Column("allocation_start", sa.String()),
    sa.Column("allocation_end", sa.String()),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_networks_name', 'name', unique=True),
)

sa.Table(
    "networking_qos_policies", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("shared", sa.Boolean(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_qos_policies_name', 'name', unique=True),
)

sa.Table(
    "networking_routers", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("external_network_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_routers_name', 'name', unique=True),
)

sa.Table(
    "networking_security_groups", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_security_groups_name', 'name', unique=True),
)

sa.Table(
    "networking_subnet_pools", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("prefixes", sa.String(), nullable=False),
    sa.Column("default_prefixlen", sa.Integer(), nullable=False),
    sa.Column("min_prefixlen", sa.Integer(), nullable=False),
    sa.Column("max_prefixlen", sa.Integer(), nullable=False),
    sa.Column("shared", sa.Boolean(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index('ix_networking_subnet_pools_name', 'name', unique=True),
)

sa.Table(
    "networking_bgp_peers", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("router_id", sa.String(), nullable=False),
    sa.Column("peer_ip", sa.String(), nullable=False),
    sa.Column("remote_as", sa.Integer(), nullable=False),
    sa.Column("local_as", sa.Integer(), nullable=False),
    sa.Column("auth_password", sa.String()),
    sa.Column("advertised_prefixes", sa.Text()),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['router_id'], ['networking_routers.id']),
    sa.Index('ix_networking_bgp_peers_name', 'name', unique=True),
    sa.Index('ix_networking_bgp_peers_project_id', 'project_id'),
)

sa.Table(
    "networking_qos_bw_limit_rules", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("qos_policy_id", sa.String(), nullable=False),
    sa.Column("max_kbps", sa.Integer(), nullable=False),
    sa.Column("max_burst_kbps", sa.Integer(), nullable=False),
    sa.Column("direction", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['qos_policy_id'], ['networking_qos_policies.id'], ondelete='CASCADE'),
)

sa.Table(
    "networking_security_group_rules", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("security_group_id", sa.String(), nullable=False),
    sa.Column("direction", sa.String(), nullable=False),
    sa.Column("ethertype", sa.String(), nullable=False),
    sa.Column("protocol", sa.String(), nullable=False),
    sa.Column("port_min", sa.String()),
    sa.Column("port_max", sa.String()),
    sa.Column("remote_ip_prefix", sa.String()),
    sa.Column("remote_group_id", sa.String()),
    sa.Column("action", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['security_group_id'], ['networking_security_groups.id'], ondelete='CASCADE'),
)

sa.Table(
    "networking_subnets", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("network_id", sa.String(), nullable=False),
    sa.Column("cidr", sa.String(), nullable=False),
    sa.Column("gateway", sa.String(), nullable=False),
    sa.Column("gateway_ip", sa.String()),
    sa.Column("allocation_start", sa.String()),
    sa.Column("allocation_end", sa.String()),
    sa.Column("dns_nameservers", sa.String()),
    sa.Column("dhcp_options_id", sa.String()),
    sa.Column("enable_dhcp", sa.Boolean(), nullable=False),
    sa.Column("ovn_dhcp_options_uuid", sa.String()),
    sa.Column("ip_version", sa.Integer(), nullable=False),
    sa.Column("ipv6_ra_mode", sa.String()),
    sa.Column("ipv6_address_mode", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['network_id'], ['networking_networks.id']),
    sa.Index('ix_networking_subnets_name', 'name', unique=True),
)

sa.Table(
    "networking_vpc_peerings", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("requester_router_id", sa.String(), nullable=False),
    sa.Column("accepter_router_id", sa.String(), nullable=False),
    sa.Column("requester_router_name", sa.String(), nullable=False),
    sa.Column("accepter_router_name", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['accepter_router_id'], ['networking_routers.id']),
    sa.ForeignKeyConstraint(['requester_router_id'], ['networking_routers.id']),
    sa.Index('ix_networking_vpc_peerings_name', 'name', unique=True),
    sa.Index('ix_networking_vpc_peerings_project_id', 'project_id'),
)

sa.Table(
    "networking_vpn_site_connections", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("project_id", sa.String(), nullable=False),
    sa.Column("router_id", sa.String(), nullable=False),
    sa.Column("peer_address", sa.String(), nullable=False),
    sa.Column("peer_id", sa.String(), nullable=False),
    sa.Column("peer_cidrs_json", sa.Text(), nullable=False),
    sa.Column("psk", sa.String(), nullable=False),
    sa.Column("ikepolicy_id", sa.String(), nullable=False),
    sa.Column("ipsecpolicy_id", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("admin_state_up", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['ipsecpolicy_id'], ['networking_ipsec_policies.id']),
    sa.ForeignKeyConstraint(['ikepolicy_id'], ['networking_ike_policies.id']),
    sa.ForeignKeyConstraint(['router_id'], ['networking_routers.id']),
    sa.Index('ix_networking_vpn_site_connections_name', 'name'),
    sa.Index('ix_networking_vpn_site_connections_project_id', 'project_id'),
)

sa.Table(
    "networking_ports", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("network_id", sa.String(), nullable=False),
    sa.Column("subnet_id", sa.String()),
    sa.Column("mac_address", sa.String(), nullable=False),
    sa.Column("ip_address", sa.String()),
    sa.Column("fixed_ip", sa.String()),
    sa.Column("ipv6_address", sa.String()),
    sa.Column("dns_name", sa.String()),
    sa.Column("logical_switch_name", sa.String()),
    sa.Column("logical_port_name", sa.String()),
    sa.Column("device_id", sa.String()),
    sa.Column("device_owner", sa.String()),
    sa.Column("security_group_ids", sa.String()),
    sa.Column("port_security_enabled", sa.Boolean(), nullable=False),
    sa.Column("qos_max_kbps", sa.Integer()),
    sa.Column("qos_burst_kbps", sa.Integer()),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['subnet_id'], ['networking_subnets.id']),
    sa.ForeignKeyConstraint(['network_id'], ['networking_networks.id']),
    sa.Index('ix_networking_ports_name', 'name', unique=True),
)

sa.Table(
    "networking_router_interfaces", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("router_id", sa.String(), nullable=False),
    sa.Column("subnet_id", sa.String(), nullable=False),
    sa.Column("router_port_name", sa.String(), nullable=False),
    sa.Column("switch_port_name", sa.String(), nullable=False),
    sa.Column("mac_address", sa.String(), nullable=False),
    sa.Column("ip_address", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['router_id'], ['networking_routers.id']),
    sa.ForeignKeyConstraint(['subnet_id'], ['networking_subnets.id']),
    sa.UniqueConstraint('switch_port_name'),
    sa.UniqueConstraint('router_port_name'),
)

sa.Table(
    "networking_floating_ips", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("floating_ip", sa.String(), nullable=False),
    sa.Column("floating_ip_address", sa.String()),
    sa.Column("fixed_ip_address", sa.String()),
    sa.Column("fixed_port_id", sa.String()),
    sa.Column("port_id", sa.String()),
    sa.Column("server_id", sa.String()),
    sa.Column("external_network_id", sa.String()),
    sa.Column("router_id", sa.String()),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['fixed_port_id'], ['networking_ports.id']),
    sa.Index('ix_networking_floating_ips_name', 'name', unique=True),
)

sa.Table(
    "networking_trunks", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("port_id", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("project_id", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['port_id'], ['networking_ports.id'], ondelete='CASCADE'),
    sa.Index('ix_networking_trunks_name', 'name', unique=True),
)

sa.Table(
    "networking_port_forwardings", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("floatingip_id", sa.String(), nullable=False),
    sa.Column("protocol", sa.String(), nullable=False),
    sa.Column("external_port", sa.Integer(), nullable=False),
    sa.Column("internal_port", sa.Integer(), nullable=False),
    sa.Column("internal_ip_address", sa.String(), nullable=False),
    sa.Column("description", sa.String()),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['floatingip_id'], ['networking_floating_ips.id'], ondelete='CASCADE'),
)

sa.Table(
    "networking_trunk_subports", metadata,
    sa.Column("id", sa.String(), primary_key=True),
    sa.Column("trunk_id", sa.String(), nullable=False),
    sa.Column("port_id", sa.String(), nullable=False),
    sa.Column("segmentation_type", sa.String(), nullable=False),
    sa.Column("segmentation_id", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['port_id'], ['networking_ports.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['trunk_id'], ['networking_trunks.id'], ondelete='CASCADE'),
)


class _Bound:
    """Lets the legacy helper below run unchanged against the migration's own connection."""

    def __init__(self, bind):
        self.bind = bind

    @contextmanager
    def begin(self):
        yield self.bind

def _legacy_ensure_columns(engine):
    inspector = inspect(engine.bind)
    table_names = inspector.get_table_names()
    with engine.begin() as conn:
        if "networking_networks" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_networks")}
            for column, ddl in (
                ("external", "BOOLEAN DEFAULT FALSE"),
                ("shared", "BOOLEAN DEFAULT FALSE"),
                ("provider", "BOOLEAN DEFAULT FALSE"),
                ("network_type", "VARCHAR"),
                ("physical_network", "VARCHAR"),
                ("bridge_mapping", "VARCHAR"),
                ("provider_bridge", "VARCHAR"),
                ("provider_cidr", "VARCHAR"),
                ("gateway_ip", "VARCHAR"),
                ("allocation_start", "VARCHAR"),
                ("allocation_end", "VARCHAR"),
                ("project_id", "VARCHAR"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_networks ADD COLUMN {column} {ddl}"))
            conn.execute(text("UPDATE networking_networks SET shared = FALSE WHERE shared IS NULL"))
        if "networking_subnets" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_subnets")}
            for column in ("gateway_ip", "allocation_start", "allocation_end", "dns_nameservers"):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_subnets ADD COLUMN {column} VARCHAR"))
            for column, ddl in (
                ("enable_dhcp", "BOOLEAN DEFAULT TRUE"),
                ("ovn_dhcp_options_uuid", "VARCHAR"),
                ("ip_version", "INTEGER DEFAULT 4"),
                ("ipv6_ra_mode", "VARCHAR"),
                ("ipv6_address_mode", "VARCHAR"),
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_subnets ADD COLUMN {column} {ddl}"))
            conn.execute(text("UPDATE networking_subnets SET gateway_ip = gateway WHERE gateway_ip IS NULL"))
            conn.execute(text("UPDATE networking_subnets SET ovn_dhcp_options_uuid = dhcp_options_id WHERE ovn_dhcp_options_uuid IS NULL"))
        if "networking_ports" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_ports")}
            for column in (
                "subnet_id",
                "fixed_ip",
                "logical_switch_name",
                "logical_port_name",
                "device_id",
                "device_owner",
                "security_group_ids",
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_ports ADD COLUMN {column} VARCHAR"))
            if "port_security_enabled" not in columns:
                conn.execute(text("ALTER TABLE networking_ports ADD COLUMN port_security_enabled BOOLEAN DEFAULT TRUE"))
            conn.execute(text("UPDATE networking_ports SET fixed_ip = ip_address WHERE fixed_ip IS NULL AND ip_address IS NOT NULL"))
        if "networking_routers" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_routers")}
            if "external_network_id" not in columns:
                conn.execute(text("ALTER TABLE networking_routers ADD COLUMN external_network_id VARCHAR"))
        if "networking_floating_ips" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_floating_ips")}
            for column in (
                "floating_ip_address",
                "fixed_ip_address",
                "port_id",
                "server_id",
                "external_network_id",
                "router_id",
            ):
                if column not in columns:
                    conn.execute(text(f"ALTER TABLE networking_floating_ips ADD COLUMN {column} VARCHAR"))
            conn.execute(text("UPDATE networking_floating_ips SET floating_ip_address = floating_ip WHERE floating_ip_address IS NULL"))
            conn.execute(text("UPDATE networking_floating_ips SET port_id = fixed_port_id WHERE port_id IS NULL"))
        if "networking_security_groups" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_security_groups")}
            if "project_id" not in columns:
                conn.execute(text("ALTER TABLE networking_security_groups ADD COLUMN project_id VARCHAR"))
            if "updated_at" not in columns:
                conn.execute(text("ALTER TABLE networking_security_groups ADD COLUMN updated_at TIMESTAMP"))
            conn.execute(text("UPDATE networking_security_groups SET updated_at = created_at WHERE updated_at IS NULL"))
        # networking_ports.qos_policy_id (added here by older releases) is not part of the Port model, so new
        # databases do not get it; existing ones keep the unused column.
        if "networking_security_group_rules" in table_names:
            columns = {column["name"] for column in inspector.get_columns("networking_security_group_rules")}
            if "remote_group_id" not in columns:
                conn.execute(text("ALTER TABLE networking_security_group_rules ADD COLUMN remote_group_id VARCHAR"))


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind, checkfirst=True)
    _legacy_ensure_columns(_Bound(bind))
    add_missing_columns(bind, metadata)


def downgrade() -> None:
    # Baseline: the tables stay (dropping a service's data is never an implicit side effect of `downgrade`).
    pass
