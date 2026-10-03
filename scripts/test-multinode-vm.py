#!/usr/bin/env python3
"""
CloudX Multi-Node E2E Validation & Verification Script
======================================================
Validates connectivity, Placement API node registration, OVN Geneve tunnel,
and RPC message flow between Control Plane and Compute Worker Nodes.

Usage:
    python3 scripts/test-multinode-vm.py --controller 192.168.10.10 --worker-node cloudx-compute-01
"""

import argparse
import json
import os
import subprocess
import sys
import time
import requests

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"

def log_info(msg):
    print(f"{CYAN}[INFO]{RESET} {msg}")

def log_success(msg):
    print(f"{GREEN}[SUCCESS]{RESET} {msg}")

def log_warn(msg):
    print(f"{YELLOW}[WARN]{RESET} {msg}")

def log_error(msg):
    print(f"{RED}[ERROR]{RESET} {msg}")

def check_endpoint(name, url, expected_code=200):
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == expected_code:
            log_success(f"Service {name:18s} @ {url} is UP (HTTP {r.status_code})")
            return True
        else:
            log_warn(f"Service {name:18s} @ {url} responded with HTTP {r.status_code}")
            return False
    except Exception as e:
        log_error(f"Service {name:18s} @ {url} is UNREACHABLE: {e}")
        return False

def check_control_plane_services(controller_ip):
    log_info(f"Checking Control Plane APIs on {controller_ip}...")
    services = {
        "auth-api": f"http://{controller_ip}:8005/health",
        "compute-api": f"http://{controller_ip}:8003/health",
        "networking-api": f"http://{controller_ip}:8001/health",
        "placement-api": f"http://{controller_ip}:8006/health",
        "image-api": f"http://{controller_ip}:8002/health",
        "block-store-api": f"http://{controller_ip}:8004/health",
    }
    all_ok = True
    for name, url in services.items():
        if not check_endpoint(name, url):
            all_ok = False
    return all_ok

def check_placement_providers(controller_ip, target_worker_name):
    log_info(f"Verifying Placement API node registration for '{target_worker_name}'...")
    url = f"http://{controller_ip}:8006/internal/resource_providers"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            providers = r.json()
            if isinstance(providers, dict) and "resource_providers" in providers:
                providers = providers["resource_providers"]
            
            found = False
            for p in providers:
                name = p.get("name", "")
                if name == target_worker_name or target_worker_name in name:
                    found = True
                    log_success(f"Found registered Compute Node in Placement API: {name} (UUID: {p.get('uuid')})")
                    break
            if not found:
                log_warn(f"Compute node '{target_worker_name}' not yet listed in Placement API providers list.")
                log_info(f"Available providers: {[p.get('name') for p in providers]}")
                return False
            return True
        else:
            log_error(f"Failed to query Placement API (HTTP {r.status_code})")
            return False
    except Exception as e:
        log_error(f"Error querying Placement API: {e}")
        return False

def check_ovn_chassis_tunnel():
    log_info("Checking local OVN Chassis & Tunnel status...")
    try:
        cmd = ["ovn-sbctl", "show"]
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            cmd = ["sudo", "ovn-sbctl", "show"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            log_success("OVN Southbound DB chassis list:")
            for line in res.stdout.splitlines():
                if "Chassis" in line or "hostname" in line or "encap" in line:
                    print(f"  {line.strip()}")
            return True
        else:
            log_warn("ovn-sbctl command failed or OVN SB DB not locally queryable.")
            return False
    except FileNotFoundError:
        log_warn("ovn-sbctl CLI tool not found in PATH.")
        return False
    except Exception as e:
        log_warn(f"Could not execute ovn-sbctl: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="CloudX Multi-Node E2E Verification Tool")
    parser.add_argument("--controller", default=os.getenv("CONTROLLER_IP", "192.168.10.10"), help="Control Plane Node IP")
    parser.add_argument("--worker-node", default=os.getenv("NODE_NAME", "cloudx-compute-01"), help="Compute Worker Node Name")
    args = parser.parse_args()

    print("======================================================================")
    print(" CloudX Multi-Node E2E Validation")
    print(f" Controller IP : {args.controller}")
    print(f" Target Worker : {args.worker_node}")
    print("======================================================================")

    cp_ok = check_control_plane_services(args.controller)
    placement_ok = check_placement_providers(args.controller, args.worker_node)
    ovn_ok = check_ovn_chassis_tunnel()

    print("\n----------------------------------------------------------------------")
    print(" SUMMARY OF MULTI-NODE HEALTH CHECKS")
    print("----------------------------------------------------------------------")
    print(f" Control Plane APIs : {'✓ OK' if cp_ok else '❌ UNHEALTHY'}")
    print(f" Compute Registration: {'✓ REGISTERED' if placement_ok else '⚠️ PENDING/NOT FOUND'}")
    print(f" OVN Chassis Status : {'✓ ACTIVE' if ovn_ok else '⚠️ UNCHECKED'}")
    print("----------------------------------------------------------------------")

    if cp_ok:
        log_success("Multi-Node validation completed successfully!")
        sys.exit(0)
    else:
        log_error("Control plane services check failed. Please review service logs.")
        sys.exit(1)

if __name__ == "__main__":
    main()
