import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", [["networking", "-h"], ["image", "-h"], ["compute", "-h"], ["block-store", "-h"]])
def test_cli_short_help(command):
    script = Path(__file__).resolve().parents[2] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), command[1]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout

@pytest.mark.parametrize(
    "command",
    [
        ["compute", "server", "create", "-h"],
        ["compute", "server", "console", "-h"],
        ["compute", "server", "console-url", "-h"],
        ["compute", "server", "metadata", "-h"],
        ["compute", "server", "user-data", "-h"],
        ["compute", "server", "set-user-data", "-h"],
        ["compute", "server", "logs", "-h"],
        ["compute", "server", "ssh-info", "-h"],
        ["compute", "server", "snapshot", "create", "-h"],
        ["compute", "server", "add-port", "-h"],
        ["compute", "server", "remove-port", "-h"],
        ["compute", "host", "check", "-h"],
        ["compute", "host", "show", "-h"],
        ["compute", "flavor", "create", "-h"],
        ["compute", "flavor", "list", "-h"],
    ],
)
def test_compute_cli_new_help(command):
    script = Path(__file__).resolve().parents[2] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout

@pytest.mark.parametrize(
    "command",
    [
        ["networking", "network", "create", "-h"],
        ["networking", "subnet", "create", "-h"],
        ["networking", "router", "set-gateway", "-h"],
        ["networking", "floating-ip", "create", "-h"],
        ["networking", "floating-ip", "associate", "-h"],
        ["networking", "security-group", "create", "-h"],
        ["networking", "security-group", "rule", "create", "-h"],
        ["networking", "port", "set-security-group", "-h"],
    ],
)
def test_networking_cli_new_help(command):
    script = Path(__file__).resolve().parents[2] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout

@pytest.mark.parametrize(
    "command",
    [
        ["block-store", "volume", "create", "-h"],
        ["block-store", "volume", "show", "-h"],
        ["block-store", "snapshot", "create", "-h"],
        ["block-store", "snapshot", "list", "-h"],
    ],
)
def test_block_store_cli_new_help(command):
    script = Path(__file__).resolve().parents[2] / "cli" / command[0]
    result = subprocess.run([sys.executable, str(script), *command[1:]], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout

def test_network_create_help_has_no_provider_nic_options():
    script = Path(__file__).resolve().parents[2] / "cli" / "networking"
    result = subprocess.run([sys.executable, str(script), "network", "create", "-h"], text=True, capture_output=True, timeout=10,
                            env={**{k: v for k, v in os.environ.items() if k != "GITHUB_ACTIONS"}, "TERMINAL_WIDTH": "200", "NO_COLOR": "1"})
    assert result.returncode == 0, result.stderr
    assert "--external" in result.stdout
    assert "--provider-bridge" not in result.stdout
    assert "--provider-cidr" not in result.stdout
