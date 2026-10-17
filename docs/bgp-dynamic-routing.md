# BGP dynamic routing (BIRD)

Sesi eBGP sungguhan antara router CloudX dan peer eksternal. Satu daemon BIRD 2 per router berjalan di netns `qbgp-<short>` pada host gateway chassis router, dikelola oleh `cloudx-bgp-worker`.

## Topologi

```
peer eksternal ---- L2 jaringan eksternal ---- br-int (port bgp<short>-out, outer_ip)
                                                  |
                                          netns qbgp-<short>: bird
                                                  |
                                    router OVN (next-hop yang diumumkan = IP gateway router)
```

- Satu speaker per router, dibuat saat peer pertama dan dihapus bersama peer terakhir.
- Speaker memakai `outer_ip` sendiri dari pool jaringan eksternal (bukan IP gateway router). Peer harus berada di L2 jaringan eksternal yang sama (tanpa multihop).
- Semua peer pada satu router berbagi satu BIRD; `local_as` harus sama (beda: 409). Router tanpa external gateway: 400.

## Perilaku

- Diumumkan: hanya `advertised_prefixes`, dengan next-hop IP gateway router agar lalu lintas masuk ke router OVN, bukan ke netns.
- Dipelajari: route dari peer dimasukkan ke router OVN lewat `lr-route-add` dan dicatat di `networking_bgp_learned_routes`. Peer `down` atau dihapus: seluruh route-nya ditarik. Route statis lain di router tidak disentuh.
- Diabaikan: route default (`0.0.0.0/0`), prefix yang sama dengan, lebih spesifik dari, atau supernet subnet router, next-hop di luar subnet jaringan eksternal, dan prefix yang sudah menjadi route statis router.
- `bird.conf` ditulis mode 0600; `birdc configure` hanya dijalankan saat isi berubah. Password tidak pernah masuk log atau argv.
- Status (`down`, `established`, `error` + `status_reason`) dan route dipelajari dilaporkan worker tiap 5 detik hanya saat berubah.

## Penggunaan

```bash
cloudx bgp-peer create isp1 --router router1 --peer-ip 172.17.0.200 \
    --local-as 65000 --remote-as 65100 --auth-password rahasia123 \
    --advertised-prefix 10.0.0.0/24
cloudx bgp-peer list          # status, status_reason, outer_ip
cloudx bgp-peer show isp1     # termasuk learned_routes
cloudx bgp-peer sync-config isp1   # bird_config (password disamarkan)
```

`sync-config` mengembalikan `bird_config`; kunci `frr_config` dipertahankan untuk kompatibilitas dan berisi nilai yang sama.

## Diagnosis

```bash
sudo journalctl -u cloudx-bgp-worker -f
sudo ip netns exec qbgp-<short> birdc -s /var/lib/cloudx/bgp/<speaker_id>/bird.ctl show protocols
sudo ip netns exec qbgp-<short> birdc -s /var/lib/cloudx/bgp/<speaker_id>/bird.ctl show route
```

Instalasi: `scripts/setup-compute-node.sh` memasang `bird2`, menonaktifkan unit `bird` bawaan, dan menulis `/etc/cloudx/bgp-worker/bgp-worker.conf` (`node_name`). Aktifkan dengan `systemctl enable --now cloudx-bgp-worker` di compute dan storage.

## Keterbatasan

- eBGP IPv4 saja; peer harus satu L2 dengan jaringan eksternal (tanpa multihop).
- Satu host per speaker, tanpa failover.
- Password MD5 disimpan plaintext.
- Tanpa BFD, IPv6, route-map kustom, graceful restart; export hanya `advertised_prefixes`.
- Route default dan prefix yang menimpa subnet router dari peer diabaikan.
- Lalu lintas masuk ke prefix privat yang di-SNAT router bergantung pada simetri balasan.
- `outer_ip` speaker memakai satu alamat dari pool jaringan eksternal.
