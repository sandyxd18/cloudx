"""
L7 Application Load Balancer driver (HAProxy).

This is only the control-plane half: it renders the config so bad input is rejected when the API call is made.
The haproxy process itself runs on a compute node, inside a netns bound to the VIP port, started by lb-worker, which
pulls the same desired state from /internal/lb-agent/<host>.
"""
from __future__ import annotations

from shared.haproxy_cfg import render

from .base import LoadBalancerDriver


class HAProxyL7Driver(LoadBalancerDriver):
    name = "haproxy_l7"
    layer = "L7"

    def __init__(self, config: dict) -> None:
        pass

    def apply(self, lb: dict, listeners: list[dict]) -> None:
        render(lb, listeners)  # raises ValueError on anything unsafe; lb-worker applies it

    def delete(self, lb: dict) -> None:
        pass  # lb-worker tears down what is no longer in its desired state

    def health(self) -> dict:
        return {"ok": True}
