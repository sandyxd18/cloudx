# VPNaaS IPsec site-to-site

VPN site-to-site sungguhan: satu strongSwan `charon` per router, berjalan di netns `qvpn-<short>` pada host gateway chassis router, dikelola oleh `cloudx-vpn-worker`.

## Topologi

```
subnet lokal -- router (OVN) --LRP-- LS vpn-<gw> --LSP-- [qvpn-<short>: "in" .2/30]
                                                           charon (strongSwan)
public-net -------------------- LS public --LSP-- [qvpn-<short>: "out" = outer_ip]
```

- Transit `/30` dari `100.101.0.0/16`: router `.1`, netns `.2`. Router mendapat route statis `peer_cidrs` via `.2`.
- `outer_ip` diambil dari pool jaringan eksternal (bukan IP gateway router). Router wajib punya external gateway (jika tidak, 400).
- `peer_cidrs` yang tumpang tindih dengan subnet router mengembalikan 409.

## Perilaku

- Membuat site connection pertama pada router membuat gateway + objek OVN; menghapus yang terakhir membersihkan semuanya (OVN, netns, port OVS, direktori, charon).
- `vpn-worker` polling `GET /internal/vpn-agent/<node_name>` tiap 5 detik, menulis `swanctl.conf` (mode 0600), `swanctl --load-all` hanya bila isi berubah, lalu melaporkan status (`ACTIVE` bila child SA INSTALLED, `DOWN`, `ERROR` + `status_reason`).
- `admin_state_up=false` mengeluarkan koneksi dari konfigurasi.

## Urutan membuat dua sisi

`peer_address` dan `peer_id` sebuah sisi adalah `outer_ip` gateway sisi lain. Buat koneksi di sisi A, baca `outer_ip` gateway-nya, buat koneksi di sisi B dengan peer itu, lalu buat ulang koneksi A dengan `outer_ip` gateway B.

## Keterbatasan

- Hanya PSK (tanpa X.509).
- Satu host per gateway, tanpa failover.
- PSK disimpan plaintext di DB dan dikembalikan oleh `/vpn/site-connections/{id}/config`.
- Tanpa remote-access VPN dan tanpa BGP di atas IPsec.
- DPD tidak dapat dikonfigurasi: bila peer dihapus, sisi yang tersisa bisa tetap `ACTIVE` sampai SA kedaluwarsa.
- Urutan pembuatan dua sisi manual (lihat di atas).

## Diagnosis

```bash
sudo ip netns exec qvpn-<short> swanctl --list-sas --uri unix:///var/lib/cloudx/vpn/<gw_id>/vici
sudo journalctl -u cloudx-vpn-worker -f
```
