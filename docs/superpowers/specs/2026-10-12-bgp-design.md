# BGP dynamic routing (BIRD) — desain

Status: disetujui user (chat, 2026-10-12). Spike: `docs/superpowers/plans/bgp-spike-notes.md` (dua BIRD 2.14 di dua netns, Established, MD5 teruji, `birdc configure` tanpa memutus sesi, tanpa profil AppArmor).

## Tujuan
Tabel `networking_bgp_peers` dan CRUD-nya sudah ada, tetapi tidak ada daemon: `sync-config` hanya menghasilkan teks. Fase ini membuatnya nyata: sesi eBGP sungguhan ke peer eksternal, `advertised_prefixes` diumumkan, dan route yang dipelajari masuk ke router OVN.

## Arsitektur (pola sama dengan VPNaaS)
- **Speaker** = satu `bird` per router, di netns `qbgp-<short>` pada host gateway chassis router, dikelola `bgp-worker` (compute dan storage, polling 5 s, `InternalAuth`).
- Satu port OVS internal di `br-int` (`iface-id` = LSP di LS jaringan eksternal router) dengan `outer_ip` sendiri dari pool jaringan eksternal (bukan IP gateway router). Tanpa jaringan transit: peer harus berada di L2 jaringan eksternal yang sama (tanpa multihop).
- `short = gw_id.split("-",1)[1][:8]` (ID `bgpsp-<12 hex>`).
- Semua peer pada router yang sama berbagi satu BIRD. `local_as` harus sama di antara peer satu router (beda → 409).
- Router tanpa external gateway → 400 saat membuat peer.

## Data
- Tabel baru `networking_bgp_speakers`: `id, router_id (unik), project_id, host, outer_network_id, outer_ip, outer_mac, status, created_at`. Dibuat saat peer pertama pada router, dihapus bersama peer terakhir (objek OVN ikut dibersihkan, rollback best-effort seperti VPN).
- Tabel baru `networking_bgp_learned_routes`: `id, peer_id, prefix, nexthop`. Dipakai API untuk tahu route OVN mana yang miliknya.
- `BGPPeer` bertambah `speaker_id` (tanpa FK, seperti `gateway_id` VPN) dan `status_reason`. `status` menjadi `down | established | error`.
- Migrasi `networking_0007` (idempoten, downgrade hanya drop tabel).

## Worker (`services/bgp_worker`)
- Per speaker: netns `qbgp-<short>`, port OVS dengan `external_ids:cloudx_bgp=<id>`, `bird -c <dir>/bird.conf -s <dir>/bird.ctl -P <dir>/bird.pid` dengan `<dir> = /var/lib/cloudx/bgp/<id>/`, ditunggu sampai socket ada. `bird.conf` ditulis 0600 oleh fungsi murni `shared/bird_cfg.py:render()`; `birdc configure` hanya bila sha256 berubah.
- Konfigurasi: `router id = outer_ip`; `protocol device`; protocol static (blackhole) untuk `advertised_prefixes` semua peer; satu `protocol bgp` per peer (`local outer_ip as local_as; neighbor peer_ip as remote_as; password`), export hanya route static dengan `bgp_next_hop = router_ext_ip` (IP gateway router, supaya lalu lintas masuk ke router OVN, bukan ke netns), import semua route kecuali default dan prefix yang menimpa subnet router.
- Status tiap tick: `birdc show protocols` → `established` atau `down`; route dipelajari lewat `birdc show route protocol <bgp> all` (parse `via`/`BGP.next_hop`). Hasil dikirim ke `POST /internal/bgp-agent/{host}/status` hanya jika berubah.
- Orphan (netns/port/dir/bird tanpa speaker di daftar) dibersihkan. Tidak pernah mencatat password ke log. `bird` dijalankan terlepas lewat `shared.proc.run` dengan shell, pid dari `bird.pid`.

## API
- `POST /bgp-peers`: memvalidasi, mengalokasikan/menemukan speaker (host dari `lrp-get-gateway-chassis` → hostname SB, helper `_get_router_host` VPN dipakai ulang), membuat LSP luar, `outer_ip` unik (menghindari FIP, gateway router, VPN gateway, speaker lain), 409 bila pool habis.
- `DELETE /bgp-peers/{id}`: hapus learned routes peer itu dari OVN, lalu speaker bila peer terakhir.
- `GET/POST /internal/bgp-agent/{host}[/status]` (`InternalAuth`): data speaker + peer aktif (`is_active`) + prefix; status menerima `{peers: {id: {status, status_reason, routes: [{prefix, nexthop}]}}}`.
- Rekonsiliasi route: untuk tiap peer, selisih `routes` vs `networking_bgp_learned_routes`; tambah → `lr-route-add <router> <prefix> <nexthop>`, hilang → `lr-route-del`. Nexthop harus berada di subnet jaringan eksternal; selain itu diabaikan. Peer `down` → semua route-nya ditarik.
- `sync-config` menghasilkan konfigurasi BIRD dengan password disamarkan (`render(mask=True)`); kunci respons tetap `frr_config` dan ditambah `bird_config`, supaya CLI/dashboard lama tidak rusak.
- CLI `cloudx bgp-peer` menampilkan `status`, `status_reason`, `outer_ip`, dan route yang dipelajari.

## Uji
- Unit (mock `ovn_cli`, `run`): objek OVN dibuat dan di-rollback, 400/409, rekonsiliasi route (tambah/hapus/ditarik saat down), `render()` (password hanya di file 0600), perintah worker eksak, orphan, status hanya saat berubah, password tak ada di log/argv. Endpoint internal lewat `TestClient` dengan `INTERNAL_TOKEN`. Mutation check untuk tiap invarian.
- Live (`cloudx-net-ops`, tanpa penyesuaian manual): router uji `zz-bgp-r` ber-gateway ke `public-net`; "ISP" = BIRD kedua di netns lain pada host yang sama, ditaruh di L2 jaringan eksternal (port OVS pada br-int dengan LSP sendiri, dibuat oleh skrip uji). Bukti: Established dua sisi, route dari ISP muncul di `lr-route-list`, ping VM → alamat di balik ISP lewat route itu, route hilang saat ISP menarik prefix atau peer dihapus, ISP menerima `advertised_prefixes` dengan next-hop IP router. Bersih-bersih dan perbandingan state `router1` sebelum/sesudah.
- Risiko yang dibuktikan di tes live: lalu lintas masuk dari ISP ke prefix privat melewati SNAT router; bila balasan tidak simetris, hasilnya dicatat sebagai keterbatasan (advertise tetap berfungsi untuk prefix publik/FIP).

## Keterbatasan (untuk release notes)
- eBGP IPv4 saja, peer harus satu L2 dengan jaringan eksternal (tanpa multihop).
- Satu host per speaker, tanpa failover.
- Password MD5 disimpan plaintext.
- Tanpa BFD, IPv6, route-map kustom, graceful restart; export tetap hanya `advertised_prefixes`.
- Route default dan prefix yang menimpa subnet router dari peer diabaikan.
- Inbound ke prefix privat yang di-SNAT router bergantung pada simetri balasan (lihat risiko).
- `sync-config` tidak lagi menghasilkan FRR; kunci `frr_config` dipertahankan untuk kompatibilitas tetapi berisi konfigurasi BIRD.

## Dependensi/instalasi
`bird2` di `scripts/setup-compute-node.sh` (nonaktifkan unit `bird` bawaan), unit `cloudx-bgp-worker`, `/etc/cloudx/bgp-worker/bgp-worker.conf` (seperti vpn-worker). Docs: halaman BGP berbahasa Indonesia, hapus klaim "no BGP transit".
