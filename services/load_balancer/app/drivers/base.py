"""
Abstract load-balancer driver.

Concrete drivers translate Octavia-like LB objects into real data-plane
rules (OVN native LBs, HAProxy configs, etc.).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LoadBalancerDriver(ABC):
    """Base interface for ALB / NLB drivers."""

    name: str = ""
    layer: str = ""  # "L4" or "L7"

    @abstractmethod
    def __init__(self, config: dict) -> None:
        """Initialise driver with backend-specific config."""

    @abstractmethod
    def create_load_balancer(self, lb: dict) -> None:
        """Provision a load balancer and assign VIP."""

    @abstractmethod
    def delete_load_balancer(self, lb: dict) -> None:
        """De-provision a load balancer and release VIP."""

    @abstractmethod
    def apply_listener(self, lb: dict, listener: dict) -> None:
        """Create or update a listener on the data plane."""

    @abstractmethod
    def delete_listener(self, lb: dict, listener: dict) -> None:
        """Remove a listener from the data plane."""

    @abstractmethod
    def apply_pool(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        """Create/update pool and members."""

    @abstractmethod
    def delete_pool(self, lb: dict, pool: dict) -> None:
        """Remove pool."""

    @abstractmethod
    def update_members(self, lb: dict, listener: dict, pool: dict, members: list[dict]) -> None:
        """Refresh pool member list without recreating the listener."""

    @abstractmethod
    def health(self) -> dict:
        """Return driver health status."""
