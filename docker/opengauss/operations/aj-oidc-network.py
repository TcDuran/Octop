#!/usr/bin/python3
"""Maintain only AJ Octop's tagged OIDC hairpin NAT rule."""

import fcntl
import ipaddress
import json
import shlex
import subprocess
import sys

TAG = "octop-aj-oidc-hairpin"
IPTABLES = ["/usr/sbin/iptables", "-w", "5", "-t", "nat"]


def command(args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=10)


def tagged_rules():
    rules = command(IPTABLES + ["-S", "PREROUTING"]).stdout.splitlines()
    result = []
    for line in rules:
        parts = shlex.split(line)
        if "--comment" in parts and parts[parts.index("--comment") + 1] == TAG:
            result.append(parts)
    return result


def remove_rules(rules):
    for parts in rules:
        command(IPTABLES + ["-D"] + parts[1:])


def source_ip():
    try:
        data = json.loads(command(["/usr/bin/docker", "inspect", "octop-opengauss-aj"]).stdout)[0]
    except (subprocess.SubprocessError, ValueError, IndexError):
        return None
    if not data["State"]["Running"]:
        return None
    raw_address = data["NetworkSettings"]["Networks"].get("bridge", {}).get("IPAddress")
    if not raw_address:
        return None
    address = ipaddress.IPv4Address(raw_address)
    if address not in ipaddress.IPv4Network("172.17.0.0/16"):
        print("AJ Octop no longer uses the expected bridge subnet; rule disabled")
        return None
    return str(address)


def main():
    if sys.argv[1:] not in ([], ["--remove"]):
        raise ValueError("usage: aj-oidc-network.py [--remove]")
    with open("/run/lock/octop-aj-oidc-network.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        rules = tagged_rules()
        source = None if sys.argv[1:] == ["--remove"] else source_ip()
        if source is None:
            remove_rules(rules)
            if rules:
                print("Removed AJ Octop OIDC NAT rule")
            return
        desired = [
            "-i",
            "docker0",
            "-s",
            source + "/32",
            "-d",
            "211.137.76.3/32",
            "-p",
            "tcp",
            "--dport",
            "8081",
            "-m",
            "comment",
            "--comment",
            TAG,
            "-j",
            "DNAT",
            "--to-destination",
            "10.51.49.8:8081",
        ]
        existing = command(IPTABLES + ["-C", "PREROUTING"] + desired, check=False)
        if existing.returncode not in (0, 1):
            raise RuntimeError("Unable to check AJ Octop OIDC NAT rule")
        if len(rules) == 1 and existing.returncode == 0:
            return
        remove_rules(rules)
        command(IPTABLES + ["-I", "PREROUTING", "1"] + desired)
        print("Applied AJ Octop OIDC NAT rule for " + source)


if __name__ == "__main__":
    main()
