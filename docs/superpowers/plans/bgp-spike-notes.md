# BIRD 2 multi-netns eBGP spike notes (BGP spike task)

The spike proved that two BIRD 2 (`bird2`) instances can run at the same time in two network namespaces (`bgA` and `bgB`) on one host (`compute-node`), each with its own configuration, PID file and UNIX control socket, form an eBGP session with TCP MD5 signature authentication, and exchange routes dynamically.

---

## 1. Findings: binary, daemonizing and AppArmor

On Ubuntu 24.04 Noble (`compute-node`):
- **Package:** `bird2` version `2.14-1build2`.
- **Binary paths:**
  - `/usr/sbin/bird` (the BIRD 2 daemon)
  - `/usr/sbin/birdc` (the BIRD CLI client)
- **Default service state:**
  - The distro's `bird.service` was disabled and stopped (`sudo systemctl disable --now bird`).
- **Daemonizing and start flags:**
  - BIRD 2 runs as a daemon by default (it forks into the background). The `-f` flag is only for foreground use.
  - The exact start flags used:
    ```bash
    sudo ip netns exec <netns> bird -c <dir>/bird.conf -s <dir>/bird.ctl -P <dir>/bird.pid
    ```
  - Because `-s <socket>` and `-P <pidfile>` point into a per-instance directory, two BIRD instances have no file descriptor or socket lock conflict at all.
  - **Socket access:** the UNIX control socket `bird.ctl` lives in the host's filesystem namespace. The CLI `birdc -s <dir>/bird.ctl` can be called directly from the host without wrapping it in `ip netns exec`.
- **AppArmor finding:**
  - `sudo aa-status` shows 121 profiles loaded, but **there is no AppArmor profile for `bird` or `/usr/sbin/bird`** (searching `/etc/apparmor.d/*bird*` only finds `thunderbird`).
  - Nothing blocks reading config or writing sockets outside `/etc/bird` and `/run/bird`. BIRD is free to use custom directories such as `/var/lib/cloudx/bgp-spike/...` without changing any AppArmor profile.

---

## 2. Topology and configuration that worked

### Network namespaces and veth
- Netns `bgA`: IP `192.0.2.1/30` on `veth-bgA`
- Netns `bgB`: IP `192.0.2.2/30` on `veth-bgB`

### Instance A configuration (`/var/lib/cloudx/bgp-spike/A/bird.conf`)
```bird
log "/var/lib/cloudx/bgp-spike/A/bird.log" all;
router id 192.0.2.1;

protocol device {
}

protocol static static4 {
    ipv4;
    route 10.201.0.0/24 blackhole;
}

protocol bgp bgp_peer {
    local 192.0.2.1 as 64513;
    neighbor 192.0.2.2 as 64512;
    password "testmd5secret";
    ipv4 {
        import all;
        export filter {
            if proto = "static4" then accept;
            reject;
        };
    };
}
```

### Instance B configuration (`/var/lib/cloudx/bgp-spike/B/bird.conf`)
```bird
log "/var/lib/cloudx/bgp-spike/B/bird.log" all;
router id 192.0.2.2;

protocol device {
}

protocol static static4 {
    ipv4;
    route 10.202.0.0/24 blackhole;
}

protocol bgp bgp_peer {
    local 192.0.2.2 as 64512;
    neighbor 192.0.2.1 as 64513;
    password "testmd5secret";
    ipv4 {
        import all;
        export filter {
            if proto = "static4" then accept;
            reject;
        };
    };
}
```

---

## 3. The exact commands that worked

1. **Start BIRD in each netns:**
   ```bash
   sudo ip netns exec bgA bird -c /var/lib/cloudx/bgp-spike/A/bird.conf -s /var/lib/cloudx/bgp-spike/A/bird.ctl -P /var/lib/cloudx/bgp-spike/A/bird.pid
   sudo ip netns exec bgB bird -c /var/lib/cloudx/bgp-spike/B/bird.conf -s /var/lib/cloudx/bgp-spike/B/bird.ctl -P /var/lib/cloudx/bgp-spike/B/bird.pid
   ```

2. **Check the protocol state (Established):**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show protocols
   birdc -s /var/lib/cloudx/bgp-spike/B/bird.ctl show protocols
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show protocols all bgp_peer
   ```

3. **See the learned routes with their next hop:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show route protocol bgp_peer
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show route all
   ```

4. **Reload the configuration at runtime without restarting the daemon:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl configure
   ```

5. **Restart a specific protocol / BGP session if needed:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl restart bgp_peer
   ```

---

## 4. Real output samples and how to parse them

### Output of `show protocols`
```text
BIRD 2.14 ready.
Name       Proto      Table      State  Since         Info
device1    Device     ---        up     23:56:37.719  
static4    Static     master4    up     23:56:37.719  
bgp_peer   BGP        ---        up     23:56:42.217  Established   
```

### Output of `show route protocol bgp_peer`
```text
BIRD 2.14 ready.
Table master4:
10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
	via 192.0.2.2 on veth-bgA
```

### Output of `show route all` (BGP attribute detail)
```text
BIRD 2.14 ready.
Table master4:
10.201.0.0/24        blackhole [static4 23:56:37.719] * (200)
	Type: static univ
10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
	via 192.0.2.2 on veth-bgA
	Type: BGP univ
	BGP.origin: IGP
	BGP.as_path: 64512
	BGP.next_hop: 192.0.2.2
	BGP.local_pref: 100
```

### Output format and automatic parsing
- **Verbose output (`birdc -v -s ...`):**
  Includes the numeric reply codes of the BIRD CLI protocol (similar to FTP/RFC 821):
  ```text
  0001 BIRD 2.14 ready.
  1007-Table master4:
       10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
       	via 192.0.2.2 on veth-bgA
  ```
- **Standard parsing:**
  The standard text format is easy to parse with simple regular expressions:
  - Table header: `^Table (\S+):`
  - Prefix line: `^\s*(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<type>\w+)\s+\[(?P<proto>\w+)[^\]]*\]\s+(?:\*|\?)\s+\((?P<pref>\d+)\)\s+\[AS(?P<aspath>[\d\s]+)[a-z]?\]`
  - Next-hop line: `^\s+via\s+(?P<nexthop>\d+\.\d+\.\d+\.\d+)\s+on\s+(?P<dev>\S+)`
  - Full BGP attributes from `show route all` as an alternative:
    - `BGP.next_hop:\s*(?P<nexthop>\S+)`
    - `BGP.as_path:\s*(?P<aspath>\S+)`
    - `BGP.local_pref:\s*(?P<local_pref>\d+)`

---

## 5. Evidence for live reload and MD5 authentication

### Live reload (`birdc configure`)
When a new route `10.201.99.0/24 blackhole` was added to `A/bird.conf` and `birdc -s A/bird.ctl configure` was run:
- Output:
  ```text
  Reading configuration from /var/lib/cloudx/bgp-spike/A/bird.conf
  Reconfigured
  ```
- Peer B received the route at once, with no interruption of the eBGP session:
  ```text
  Table master4:
  10.201.99.0/24       unicast [bgp_peer 23:57:14.232] * (100) [AS64513i]
  	via 192.0.2.1 on veth-bgB
  10.201.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64513i]
  	via 192.0.2.1 on veth-bgB
  ```

### Proof of MD5 authentication
When the password in B's configuration was changed to `wrongpassword123` and reloaded:
- The eBGP session immediately failed to form / dropped:
  - The protocol state changed from `Established` to:
    ```text
    bgp_peer   BGP        ---        start  23:57:16.237  Active        
    ```
  - The log recorded the dropped connection: `Received: Other configuration change`.
- As soon as the password on B was restored to `testmd5secret` and `birdc configure` was run:
  - The eBGP session went straight back to:
    ```text
    bgp_peer   BGP        ---        up     23:57:21.147  Established   
    ```
- This proves that the TCP MD5 signature protection is really active and validated by the Linux kernel.

---

## 6. Surprises and implementation notes

1. **`birdc` flag syntax:**
   - `birdc --help` is not valid because `birdc` does not support GNU long options such as `--help` (only `-s`, `-v`, `-r`, `-l`).
2. **No need to unshare the mount namespace:**
   - Unlike the distro's strongSwan/charon, which hard-codes `/var/run/charon.pid` unless `charon-systemd` is used, BIRD 2 fully supports the custom `-c`, `-s` and `-P` options, so mount namespace isolation (`unshare -m`) is not needed at all.
3. **Control socket from outside the netns:**
   - The UNIX control socket `bird.ctl` can be reached directly by a script/worker in the host namespace without `ip netns exec`. Just pass `-s <path_to_socket>`.

---

## 7. Cleanup status

- Both bird processes were stopped through the PID files and pkill.
- Netns `bgA` and `bgB` and their veths were deleted without touching any other netns (`qlb-7f129cec` stayed intact).
- The spike directory `/var/lib/cloudx/bgp-spike` was deleted completely.
- The `bird2` package stays installed on `compute-node` with the systemd unit `bird.service` `disabled` and `inactive`.
- No OVN/OVS/VM/FIP configuration was touched.
