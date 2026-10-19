# Container (setara Zun)

Container tunggal di node compute, masing-masing dengan IP sendiri di jaringan tenant. Cluster ada di `k8s-engine`.

## Arsitektur

- `container-api` (controller, port 8018): tabel `ct_containers` dan `ct_hosts`.
- `container-worker` (tiap node compute, root): polling `GET /internal/ct-agent/<host>` tiap 5 detik, merekonsiliasi dengan Docker, lalu melapor ke `POST /internal/ct-agent/<host>/report` (status, error, 200 baris log terakhir).
- Host dipilih saat create: node hidup (heartbeat < 30 detik) dengan container paling sedikit. Tidak ada yang hidup: 503.
- Jaringan: container berjalan dengan `--network none`. API membuat port OVN lewat networking-api (security group berlaku), worker memasang OVS internal port di `br-int` ke netns container. Setelah restart, plumbing diulang karena PID berubah. Tidak ada NAT.
- Docker dipaksa `iptables:false`, `bridge:none` (`/etc/docker/daemon.json`) supaya NAT router tidak tersentuh.

## Keamanan

Tanpa `--privileged`, host mount, host network. Selalu `--cap-drop ALL` (hanya CHOWN, DAC_OVERRIDE, FOWNER, SETUID, SETGID, NET_BIND_SERVICE ditambahkan kembali), `no-new-privileges`, `--pids-limit 256`, batas CPU (0.1-4) dan memori (64-4096 MB) wajib. Image divalidasi regex, perintah dikirim sebagai list argumen (tanpa shell).

## Konfigurasi

Controller: `container_api_url`. Tiap node worker, di `/etc/cloudx/container-worker/container-worker.conf` (dengan `[DEFAULT]`): `container_api_url`, `container_host` (sama dengan `node_name`). `install-systemd.sh` memasang Docker hanya di node yang punya `container_host`.

## CLI

```
cloudx container create web --image nginx:1.27 --network <net> --cpu 0.5 --memory 128 --env A=1 --command sleep --command 10
cloudx container list | show | start | stop | restart | logs | delete
```

## Batasan

Exec, volume block-store, port publish dan floating IP, build image, registry privat, restart policy, resize, konsol interaktif belum ada. Pull image butuh internet dari node. Container baru tidak punya jaringan beberapa detik sampai worker memasangnya, jadi proses yang langsung memakai jaringan saat start bisa gagal.