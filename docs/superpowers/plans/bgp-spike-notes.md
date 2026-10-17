# Catatan Spike BIRD 2 Multi-Netns eBGP (Task Spike BGP)

Spike membuktikan bahwa dua instance BIRD 2 (`bird2`) dapat berjalan bersamaan di dua network namespace (`bgA` dan `bgB`) pada satu host (`compute-node`), masing-masing dengan konfigurasi, PID file, dan UNIX control socket terpisah, berhasil membentuk sesi eBGP dengan autentikasi TCP MD5 signature, serta bertukar route secara dinamis.

---

## 1. Temuan Biner, Daemonize & AppArmor

Pada Ubuntu 24.04 Noble (`compute-node`):
- **Package:** `bird2` versi `2.14-1build2`.
- **Path Biner:**
  - `/usr/sbin/bird` (daemon BIRD 2)
  - `/usr/sbin/birdc` (CLI client BIRD)
- **Status Service bawaan:**
  - Daemon bawaan `bird.service` berhasil di-disable dan dimatikan (`sudo systemctl disable --now bird`).
- **Daemonize & Flag Start:**
  - BIRD 2 secara default langsung berjalan sebagai daemon (me-fork ke background). Flag `-f` hanya dipakai bila ingin foreground.
  - Flag start persis yang digunakan:
    ```bash
    sudo ip netns exec <netns> bird -c <dir>/bird.conf -s <dir>/bird.ctl -P <dir>/bird.pid
    ```
  - Karena parameter `-s <socket>` dan `-P <pidfile>` diarahkan ke direktori per-instance, dua instance BIRD tidak mengalami konflik file descriptor atau socket lock sama sekali.
  - **Socket Access:** UNIX control socket `bird.ctl` berada di filesystem namespace host. CLI `birdc -s <dir>/bird.ctl` dapat dipanggil langsung dari host tanpa perlu dibungkus `ip netns exec`.
- **Temuan AppArmor:**
  - Pengecekan `sudo aa-status` menunjukkan 121 profil termuat, namun **tidak ada profil AppArmor untuk `bird` atau `/usr/sbin/bird`** (pencarian di `/etc/apparmor.d/*bird*` hanya menemukan `thunderbird`).
  - Tidak ada pemblokiran pembacaan config atau penulisan socket di luar `/etc/bird` dan `/run/bird`. BIRD bebas menggunakan direktori kustom seperti `/var/lib/cloudx/bgp-spike/...` tanpa modifikasi profil AppArmor.

---

## 2. Topologi & Konfigurasi yang Berhasil

### Network Namespace & veth
- Netns `bgA`: IP `192.0.2.1/30` pada `veth-bgA`
- Netns `bgB`: IP `192.0.2.2/30` pada `veth-bgB`

### Konfigurasi Instance A (`/var/lib/cloudx/bgp-spike/A/bird.conf`)
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

### Konfigurasi Instance B (`/var/lib/cloudx/bgp-spike/B/bird.conf`)
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

## 3. Perintah Persis yang Berhasil

1. **Menjalankan BIRD di masing-masing netns:**
   ```bash
   sudo ip netns exec bgA bird -c /var/lib/cloudx/bgp-spike/A/bird.conf -s /var/lib/cloudx/bgp-spike/A/bird.ctl -P /var/lib/cloudx/bgp-spike/A/bird.pid
   sudo ip netns exec bgB bird -c /var/lib/cloudx/bgp-spike/B/bird.conf -s /var/lib/cloudx/bgp-spike/B/bird.ctl -P /var/lib/cloudx/bgp-spike/B/bird.pid
   ```

2. **Memeriksa status protokol (Established):**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show protocols
   birdc -s /var/lib/cloudx/bgp-spike/B/bird.ctl show protocols
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show protocols all bgp_peer
   ```

3. **Melihat route yang dipelajari beserta next-hop:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show route protocol bgp_peer
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl show route all
   ```

4. **Reload konfigurasi runtime tanpa restart daemon:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl configure
   ```

5. **Restart protokol / sesi BGP tertentu jika diperlukan:**
   ```bash
   birdc -s /var/lib/cloudx/bgp-spike/A/bird.ctl restart bgp_peer
   ```

---

## 4. Contoh Output Nyata & Metode Parsing

### Output `show protocols`
```text
BIRD 2.14 ready.
Name       Proto      Table      State  Since         Info
device1    Device     ---        up     23:56:37.719  
static4    Static     master4    up     23:56:37.719  
bgp_peer   BGP        ---        up     23:56:42.217  Established   
```

### Output `show route protocol bgp_peer`
```text
BIRD 2.14 ready.
Table master4:
10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
	via 192.0.2.2 on veth-bgA
```

### Output `show route all` (Detail atribut BGP)
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

### Format Output & Metode Parsing Otomatis
- **Output Verbose (`birdc -v -s ...`):**
  Menyertakan status numeric reply code protokol CLI BIRD (mirip FTP/RFC 821):
  ```text
  0001 BIRD 2.14 ready.
  1007-Table master4:
       10.202.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64512i]
       	via 192.0.2.2 on veth-bgA
  ```
- **Parsing Standar:**
  Format teks standar mudah diparse dengan pola regular expression sederhana:
  - Header tabel: `^Table (\S+):`
  - Baris prefix: `^\s*(?P<prefix>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<type>\w+)\s+\[(?P<proto>\w+)[^\]]*\]\s+(?:\*|\?)\s+\((?P<pref>\d+)\)\s+\[AS(?P<aspath>[\d\s]+)[a-z]?\]`
  - Baris next-hop: `^\s+via\s+(?P<nexthop>\d+\.\d+\.\d+\.\d+)\s+on\s+(?P<dev>\S+)`
  - Alternatif atribut BGP lengkap dari `show route all`:
    - `BGP.next_hop:\s*(?P<nexthop>\S+)`
    - `BGP.as_path:\s*(?P<aspath>\S+)`
    - `BGP.local_pref:\s*(?P<local_pref>\d+)`

---

## 5. Bukti Live Reload & Pembuktian MD5 Authentication

### Live Reload (`birdc configure`)
Ketika route baru `10.201.99.0/24 blackhole` ditambahkan ke `A/bird.conf` dan dieksekusi `birdc -s A/bird.ctl configure`:
- Output:
  ```text
  Reading configuration from /var/lib/cloudx/bgp-spike/A/bird.conf
  Reconfigured
  ```
- Peer B langsung menerima route tersebut secara instan tanpa ada pemutusan sesi eBGP:
  ```text
  Table master4:
  10.201.99.0/24       unicast [bgp_peer 23:57:14.232] * (100) [AS64513i]
  	via 192.0.2.1 on veth-bgB
  10.201.0.0/24        unicast [bgp_peer 23:56:42.217] * (100) [AS64513i]
  	via 192.0.2.1 on veth-bgB
  ```

### Pembuktian MD5 Authentication
Ketika password pada konfigurasi B diubah menjadi `wrongpassword123` lalu di-reload:
- Sesi eBGP langsung gagal terbentuk / terputus:
  - Status protokol berubah dari `Established` menjadi:
    ```text
    bgp_peer   BGP        ---        start  23:57:16.237  Active        
    ```
  - Log mencatat pemutusan koneksi TCP MD5 mismatch: `Received: Other configuration change`.
- Begitu password di B dikembalikan ke `testmd5secret` dan dieksekusi `birdc configure`:
  - Sesi eBGP langsung kembali ke status:
    ```text
    bgp_peer   BGP        ---        up     23:57:21.147  Established   
    ```
- Hal ini membuktikan bahwa perlindungan TCP MD5 signature benar-benar aktif dan divalidasi oleh kernel Linux.

---

## 6. Hal Tak Terduga & Catatan Implementasi

1. **CLI `birdc` flag sintaks:**
   - `birdc --help` tidak valid karena `birdc` tidak mendukung GNU long option `--help` (hanya `-s`, `-v`, `-r`, `-l`).
2. **Mount namespace tidak perlu di-unshare:**
   - Berbeda dengan strongSwan/charon bawaan yang menuntut hardcoded `/var/run/charon.pid` kecuali memakai `charon-systemd`, BIRD 2 sepenuhnya mendukung opsi `-c`, `-s`, dan `-P` kustom, sehingga isolasi mount namespace (`unshare -m`) sama sekali tidak dibutuhkan.
3. **Control socket dari luar netns:**
   - UNIX control socket `bird.ctl` dapat diakses langsung oleh script/worker di namespace host tanpa perlu menjalankan `ip netns exec`. Cukup sertakan flag `-s <path_ke_socket>`.

---

## 7. Status Pembersihan (Cleanup)

- Kedua proses bird berhasil dimatikan via PID file dan pkill.
- Netns `bgA` dan `bgB` beserta veth dihapus tanpa menyentuh netns lain (`qlb-7f129cec` tetap utuh).
- Direktori spike `/var/lib/cloudx/bgp-spike` dihapus bersih.
- Paket `bird2` tetap terpasang pada `compute-node` dengan unit systemd `bird.service` dalam status `disabled` dan `inactive`.
- Tidak ada konfigurasi OVN/OVS/VM/FIP yang tersentuh.
