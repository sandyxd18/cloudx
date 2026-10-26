# strongSwan multi-netns spike notes (Task 0)

The spike proved that two separate strongSwan instances can run at the same time in two different netns on one host (`compute-node`) without PID, socket or port conflicts, and form an IKEv2 PSK IPsec tunnel that carries dummy subnet traffic encrypted with ESP.

---

## 1. Findings: the charon binary and AppArmor

On Ubuntu 24.04 (`compute-node`):
- The package `charon-systemd` provides the binary `/usr/sbin/charon-systemd`. It is designed for swanctl/vici and **does not use a hard-coded pidfile** (`/var/run/charon.pid`), so it can run in parallel in each netns without mount namespace isolation.
- The package `strongswan-charon` provides the binary `/usr/lib/ipsec/charon`. It has a hard-coded `/var/run/charon.pid`. Run in two netns without mount namespace isolation, the second instance fails (`charon already running ('/var/run/charon.pid' exists)`). Running it in several netns needs mount namespace isolation:
  ```bash
  ip netns exec <ns> unshare -m sh -c "mount -t tmpfs none /var/run && env STRONGSWAN_CONF=... /usr/lib/ipsec/charon"
  ```
- **Recommendation for `vpn_worker`:** use `/usr/sbin/charon-systemd`, because it is lighter, designed for swanctl/vici, and needs no unshare mount.
- **AppArmor note:** the distro's AppArmor profiles for `swanctl` and `charon` limit reading files/sockets to `/etc/swanctl` and `/run/charon.vici`. To test a custom socket in `/tmp/spike/*/vici` and a dynamic config:
  ```bash
  sudo ln -sf /etc/apparmor.d/usr.lib.ipsec.charon /etc/apparmor.d/disable/
  sudo ln -sf /etc/apparmor.d/usr.sbin.swanctl /etc/apparmor.d/disable/
  sudo apparmor_parser -R /etc/apparmor.d/usr.lib.ipsec.charon 2>/dev/null || true
  sudo apparmor_parser -R /etc/apparmor.d/usr.sbin.swanctl 2>/dev/null || true
  ```

---

## 2. Configuration structure that worked

### strongswan.conf (`/tmp/spike/A/strongswan.conf` & `B`)
```
charon-systemd {
    journal {
        default = -1
    }
}
charon {
    load_modular = yes
    plugins {
        include /etc/strongswan.d/charon/*.conf
        vici {
            socket = unix:///tmp/spike/A/vici
        }
    }
}
include /etc/strongswan.d/*.conf
```
*Note:* `load_modular = yes` and `include /etc/strongswan.d/charon/*.conf` are required so that the crypto and netlink plugins load completely.

### swanctl.conf (`/tmp/spike/A/swanctl.conf`)
```
connections {
    spike-vpn {
        local_addrs = 192.0.2.1
        remote_addrs = 192.0.2.2
        version = 2
        proposals = aes256-sha256-modp2048
        local {
            auth = psk
            id = 192.0.2.1
        }
        remote {
            auth = psk
            id = 192.0.2.2
        }
        children {
            spike-child {
                local_ts = 10.201.0.0/24
                remote_ts = 10.202.0.0/24
                esp_proposals = aes256-sha256
                start_action = none
            }
        }
    }
}

secrets {
    ike-spike {
        id-1 = 192.0.2.1
        id-2 = 192.0.2.2
        secret = "SpikePreSharedKey2026"
    }
}
```

---

## 3. The exact commands that worked

1. **Run a charon daemon per netns:**
   ```bash
   sudo ip netns exec spA env STRONGSWAN_CONF=/tmp/spike/A/strongswan.conf /usr/sbin/charon-systemd &
   sudo ip netns exec spB env STRONGSWAN_CONF=/tmp/spike/B/strongswan.conf /usr/sbin/charon-systemd &
   ```

2. **Load the swanctl configuration:**
   *Syntax note:* the swanctl commands (`--load-all`, `--list-sas`, `--initiate`) must come before the option flags (`--file`, `--uri`), or use the env `SWANCTL_DIR`.
   ```bash
   sudo ip netns exec spA swanctl --load-all --file /tmp/spike/A/swanctl.conf --uri unix:///tmp/spike/A/vici
   sudo ip netns exec spB swanctl --load-all --file /tmp/spike/B/swanctl.conf --uri unix:///tmp/spike/B/vici
   ```

3. **Initiate the IPsec tunnel:**
   ```bash
   sudo ip netns exec spA swanctl --initiate --child spike-child --uri unix:///tmp/spike/A/vici
   ```

4. **Verify the SA state:**
   ```bash
   sudo ip netns exec spA swanctl --list-sas --uri unix:///tmp/spike/A/vici
   sudo ip netns exec spB swanctl --list-sas --uri unix:///tmp/spike/B/vici
   ```

5. **Test the tunnel traffic and verify ESP encryption:**
   ```bash
   sudo ip netns exec spA tcpdump -c 5 -ni vethA esp &
   sudo ip netns exec spA ping -c 3 -I 10.201.0.1 10.202.0.1
   ```

---

## 4. Execution evidence (live lab output)

### A. Output of `swanctl --list-sas` (spA)
```
spike-vpn: #1, ESTABLISHED, IKEv2, 1186757f63ee8266_i* 29ef0d170874e0c0_r
  local  '192.0.2.1' @ 192.0.2.1[4500]
  remote '192.0.2.2' @ 192.0.2.2[4500]
  AES_CBC-256/HMAC_SHA2_256_128/PRF_HMAC_SHA2_256/MODP_2048
  established 0s ago, rekeying in 14062s
  spike-child: #1, reqid 1, INSTALLED, TUNNEL, ESP:AES_CBC-256/HMAC_SHA2_256_128
    installed 0s ago, rekeying in 3243s, expires in 3960s
    in  c463ba74,      0 bytes,     0 packets
    out cb53f92c,      0 bytes,     0 packets
    local  10.201.0.0/24
    remote 10.202.0.0/24
```

### B. Output of `swanctl --list-sas` (spB)
```
spike-vpn: #1, ESTABLISHED, IKEv2, 1186757f63ee8266_i 29ef0d170874e0c0_r*
  local  '192.0.2.2' @ 192.0.2.2[4500]
  remote '192.0.2.1' @ 192.0.2.1[4500]
  AES_CBC-256/HMAC_SHA2_256_128/PRF_HMAC_SHA2_256/MODP_2048
  established 0s ago, rekeying in 12996s
  spike-child: #1, reqid 1, INSTALLED, TUNNEL, ESP:AES_CBC-256/HMAC_SHA2_256_128
    installed 0s ago, rekeying in 3318s, expires in 3960s
    in  cb53f92c,      0 bytes,     0 packets
    out c463ba74,      0 bytes,     0 packets
    local  10.202.0.0/24
    remote 10.201.0.0/24
```

### C. Ping output (spA to spB over the dummy subnets)
```
PING 10.202.0.1 (10.202.0.1) from 10.201.0.1 : 56(84) bytes of data.
64 bytes from 10.202.0.1: icmp_seq=1 ttl=64 time=0.104 ms
64 bytes from 10.202.0.1: icmp_seq=2 ttl=64 time=0.107 ms
64 bytes from 10.202.0.1: icmp_seq=3 ttl=64 time=0.110 ms

--- 10.202.0.1 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 2053ms
rtt min/avg/max/mdev = 0.104/0.107/0.110/0.002 ms
```

### D. Output of `tcpdump -c 5 -ni vethA esp`
```
listening on vethA, link-type EN10MB (Ethernet), snapshot length 262144 bytes
12:16:01.103328 IP 192.0.2.1 > 192.0.2.2: ESP(spi=0xcb53f92c,seq=0x1), length 136
12:16:01.103384 IP 192.0.2.2 > 192.0.2.1: ESP(spi=0xc463ba74,seq=0x1), length 136
12:16:02.132352 IP 192.0.2.1 > 192.0.2.2: ESP(spi=0xcb53f92c,seq=0x2), length 136
12:16:02.132407 IP 192.0.2.2 > 192.0.2.1: ESP(spi=0xc463ba74,seq=0x2), length 136
12:16:03.156401 IP 192.0.2.1 > 192.0.2.2: ESP(spi=0xcb53f92c,seq=0x3), length 136
5 packets captured
6 packets received by filter
0 packets dropped by kernel
```
The ICMP traffic between the subnets 10.201.0.1 and 10.202.0.1 is fully encrypted into ESP packets (protocol 50) with SPIs `0xcb53f92c` and `0xc463ba74`.
