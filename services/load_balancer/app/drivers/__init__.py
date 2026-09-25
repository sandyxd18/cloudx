"""
Load Balancer driver registry.

Maps `lb_type` values to concrete data-plane implementations.
"""
from __future__ import annotations

from typing import Type

from .base import LoadBalancerDriver  # noqa: F401
from .ovn_l4 import OVNL4Driver
from .haproxy_l7 import HAProxyL7Driver

_REGISTRY: dict[str, Type[LoadBalancerDriver]] = {
    "NLB": OVNL4Driver,
    "ALB": HAProxyL7Driver,
}


def get_driver(name: str) -> Type[LoadBalancerDriver]:
    driver_cls = _REGISTRY.get(name.upper())
    if not driver_cls:
        raise ValueError(f"Unknown load balancer type: {name!r}. Available: {list(_REGISTRY.keys())}")
    return driver_cls


def list_drivers() -> list[str]:
    return list(_REGISTRY.keys())
