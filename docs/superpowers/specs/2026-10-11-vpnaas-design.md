# VPNaaS IPsec site-to-site nyata: desain

Tanggal: 2026-10-11. Fase 3, item 3 (setelah replikasi volume dan VPC peering).

## Tujuan

Hari ini VPNaaS hanya menyimpan record (IKE policy, IPsec policy, site connection) dan menghasilkan teks
`ipsec.conf`. Tidak ada tunnel yang berjalan. Target: koneksi VPN benar-benar naik dan trafik antara subnet
router lokal dan `peer_cidrs` lewat tunnel IPsec terenkripsi.

Kriteria sukses (live, di lab):
- Dua router CloudX (A = `router1`, B = `zz-vpn-r`, keduanya dengan external gateway ke `public-net`) saling
  terhubung lewat `public-net`. Masing-masing punya koneksi cermin.
- VM di belakang A ping VM di belakang B. `tcpdump` di `public-net` menunjukkan ESP, bukan ICMP.
- Hapus koneksi: ping gagal lagi, dan netns, port OVS, objek OVN, serta daemon bersih.
- Status koneksi di API mengikuti kenyataan: `PENDING_CREATE`, `ACTIVE`, `DOWN`.

Asumsi: lab tidak punya situs remote nyata, jadi sisi remote adalah router CloudX kedua. `vps-an` tidak dipakai.

## Di luar lingkup (masuk keterbatasan di release notes)

- Hanya PSK. Tanpa sertifikat X.509.
- Satu host per gateway, tanpa failover.
- PSK tersimpan plaintext di DB (sama seperti sekarang).
- Tanpa VPN remote-access. Tanpa BGP over IPsec.
- Tanpa dead-peer-detection yang dapat diatur. Dipakai default strongSwan (`dpd_delay`).

## Keputusan

Endpoint IPsec berjalan di **namespace per router** (dipilih pengguna). Semua sisi namespace diikat ke OVN
dengan pola `lb-worker` (port internal di `br-int` dengan `external_ids:iface-id`), sehingga OVN mengurus L2/L3
dan host tidak mendapat NAT atau aturan tambahan.

Ditolak: veth ke `br-ex` dengan MASQ di host (melewati OVN, ARP/aturan host bisa bentrok), dan strongSwan di
root namespace dengan xfrm policy (satu daemon bersama, menyentuh stack host).

## Topologi

```
VM subnet lokal -- router (OVN) --LRP-- LS vpn-<gw> --LSP-- [qvpn-<gw>: iface "in"]
                                                              charon (strongSwan)
public-net ----------------------LS public --LSP-- [qvpn-<gw>: iface "out", IP nyata dari public-net]
```

- Sisi dalam: logical switch transit `vpn-<gw>` tersambung ke router lewat LRP. IP transit dari /30 di
  `100.101.0.0/16` (`.1` router, `.2` namespace).
- Sisi luar: LSP di jaringan external router. IP-nya alamat IKE lokal (`outer_ip`), bukan IP gateway router,
  agar tidak bentrok dengan ARP milik OVN.
- Router menerima rute statis `peer_cidrs` via IP transit namespace. Namespace menerima rute subnet lokal via
  `.1`, dan default via gateway `public-net`.
- `ip_forward=1` di namespace.

## Komponen

### 1. Model dan migrasi (`networking_0006`)

Tabel baru `networking_vpn_gateways`, satu baris per router:

| kolom | arti |
|---|---|
| `id`, `router_id` (unik), `project_id` | identitas |
| `host` | node tempat namespace berjalan |
| `transit_cidr` | /30 dari `100.101.0.0/16` |
| `outer_network_id`, `outer_ip` | jaringan dan IP luar (dari subnet external) |
| `inner_mac`, `outer_mac` | MAC stabil (`stable_mac`) |
| `status` | `PENDING_CREATE`, `ACTIVE`, `ERROR` |

`networking_vpn_site_connections` mendapat kolom `gateway_id`, `status` dengan nilai
`PENDING_CREATE|ACTIVE|DOWN|ERROR`, dan `status_reason`. Gateway dibuat saat koneksi pertama pada sebuah router
dan dihapus saat koneksi terakhir hilang.

### 2. API networking (`routers/vpn.py`)

Create connection:
1. Router milik caller. Router harus punya external gateway, jika tidak 400.
2. Tolak 409 bila `peer_cidrs` tumpang tindih dengan subnet router, atau antar dirinya.
3. Pilih atau buat gateway (host = host gateway chassis router; transit /30 baru, 409 bila pool habis).
4. Buat objek OVN idempotent: LS `vpn-<gw>`, LRP router, LSP dalam, LSP luar, rute statis `peer_cidrs`.
   Gagal di tengah: rollback DB dan hapus objek OVN yang sudah dibuat (best-effort).
5. Status `PENDING_CREATE`. Worker mengubahnya menjadi `ACTIVE` atau `DOWN`.

Delete connection: hapus rute `peer_cidrs`; bila itu koneksi terakhir pada gateway, hapus LSP, LS, LRP, lalu
gateway. Worker membersihkan namespace pada tick berikutnya.

`admin_state_up=false` menghapus koneksi dari konfigurasi worker (tunnel turun), objek OVN tetap.

Endpoint internal (`require_internal`, token `X-Internal-Token`):
- `GET /internal/vpn-agent/{host}`: gateway milik host beserta koneksi, kebijakan, dan PSK.
- `POST /internal/vpn-agent/{host}/status`: `{connection_id: ACTIVE|DOWN, reason}`.

`POST /vpn/site-connections/{id}/config` tetap ada (teks hasil render yang sama dengan worker, PSK disamarkan).

### 3. Render konfigurasi (`shared/swanctl_cfg.py`)

Fungsi murni `render(gateway, connections) -> str` menghasilkan `swanctl.conf` (IKEv2, PSK, proposal dari
IKE/IPsec policy, `local_addrs = outer_ip`, `remote_addrs = peer_address`, `local_ts` = subnet router,
`remote_ts = peer_cidrs`, `start_action = start`). Nilai sudah dibatasi alfabet aman oleh skema `VPNSiteConnectionIn`.
Fungsi ini juga dipakai endpoint `/config`.

### 4. `vpn-worker` (`services/vpn_worker`)

Berjalan di compute dan storage. Config dari `/etc/cloudx/vpn-worker/vpn-worker.conf` (`node_name`, bukan env di
systemd). Polling `/internal/vpn-agent/<host>` tiap 5 detik. Untuk tiap gateway:
- netns `qvpn-<gw8>`, dua port internal di `br-int` (`iface-id` = nama LSP dalam dan luar), MAC dan IP dari API.
- `charon` per namespace dengan `strongswan.conf`, direktori dan soket vici sendiri di
  `/var/lib/cloudx/vpn/<gw>/`, dijalankan lewat `ip netns exec`.
- `swanctl.conf` ditulis mode 0600 lalu `swanctl --load-all`. Hash konfigurasi menghindari reload tak perlu.
- Tiap tick: `swanctl --list-sas` menentukan `ACTIVE` bila ada child SA terpasang, selain itu `DOWN`. Perubahan
  status dilaporkan ke API.
- Namespace, port OVS, direktori, dan proses `charon` yang tidak lagi diinginkan dibersihkan.
- PSK tidak pernah masuk log.

Unit systemd `cloudx-vpn-worker` di compute dan storage. Paket `strongswan-swanctl` dan `charon-systemd`
(tanpa memulai daemon bawaan) ditambahkan ke skrip instalasi compute dan storage.

### 5. CLI dan docs

CLI `vpn` sudah ada. Tambahkan kolom `status`, `outer_ip` di `show`, serta `status_reason`. Docs diperbarui:
hapus klaim control-plane only, jelaskan topologi, keterbatasan, dan cara diagnosis (`swanctl --list-sas` di
`ip netns exec qvpn-*`).

## Penanganan error

- OVN gagal saat create: rollback penuh, 502 sesuai pola `ovn_cli`.
- Worker tidak bisa memulai `charon` atau memuat konfigurasi: koneksi `ERROR` dengan `status_reason`, tick
  berikutnya mencoba lagi.
- Peer tidak terjangkau: koneksi `DOWN`, strongSwan terus mencoba (`start_action = start`).
- Host gateway mati: koneksi tetap `ACTIVE` di DB sampai worker melapor. Tidak ada failover (keterbatasan).

## Pengujian

- Unit render: `swanctl.conf` (proposal, TS, PSK) lewat assert.
- Unit API dengan `ovn_cli.ovn` di-mock: objek OVN yang dibuat (LS, LRP, dua LSP, rute), 400 tanpa external
  gateway, 409 CIDR tumpang tindih, rollback membersihkan objek, gateway terakhir dihapus bersama koneksi
  terakhir, transit /30 unik per gateway.
- Unit worker dengan `run` di-mock: urutan perintah netns/port/charon, tidak reload bila hash sama, pembersihan
  yatim, pelaporan status hanya saat berubah.
- Mutasi: bukti tes gagal bila guard tumpang tindih dan pembersihan rollback dicabut.
- Live: skenario kriteria sukses di atas, ditambah cek bersih (`ip netns`, `ovs-vsctl show`, `ovn-nbctl`).

## Risiko

Dua `charon` terpisah di dua netns pada host yang sama dengan `strongswan.conf` masing-masing belum diuji di
lab. Langkah pertama rencana adalah spike singkat untuk memastikannya sebelum worker ditulis. Bila gagal,
fallback: satu `charon` per host, satu koneksi swanctl per namespace, dengan `ip xfrm` di namespace.
