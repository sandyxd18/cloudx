"""
Load-balancer driver interface.

A driver gets the whole desired state of one LB and makes the data plane match it (reconcile, not incremental calls).
`listeners` is a list of dicts:
  {id, protocol, protocol_port, certificate_id, default_pool_id,
   pools: [{id, lb_algorithm, members: [{id, address, protocol_port, weight, operating_status}], health_monitor: {...}|None}],
   policies: [{action, redirect_pool_id, position, rules: [{type, compare_type, value}]}]}
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LoadBalancerDriver(ABC):
    name: str = ""
    layer: str = ""  # "L4" or "L7"

    @abstractmethod
    def __init__(self, config: dict) -> None: ...

    @abstractmethod
    def apply(self, lb: dict, listeners: list[dict]) -> None:
        """Make the data plane match this LB."""

    @abstractmethod
    def delete(self, lb: dict) -> None:
        """Remove everything of this LB from the data plane."""

    @abstractmethod
    def health(self) -> dict:
        """Return driver health status."""
