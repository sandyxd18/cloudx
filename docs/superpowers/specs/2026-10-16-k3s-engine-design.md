# K8s engine nyata (K3s) - desain

Status: disetujui user (chat, 2026-10-16).

## Tujuan
`services/k8s_engine` sekarang hanya CRUD stub: cluster langsung `ACTIVE`, tanpa VM, kubeconfig palsu. Fase ini membuat cluster K3s sungguhan di VM CloudX.

## Keputusan
- K3s nyata di VM CloudX (master + worker).
- K3s di-bake ke image Glance (`k3s-ubuntu`); VM tidak butuh internet.
- Deteksi siap lewat callback dari VM (controller tidak punya route ke jaringan tenant; hasil spike).

## Komponen
1. `scripts/build-k3s-image.sh`: di compute, ambil cloud image Ubuntu, mount dengan `qemu-nbd`, salin binary K3s + tarball airgap + installer, unggah ke Glance sebagai `k3s-ubuntu`. Dijalankan manual sekali.
2. `k8s-engine-api`: CRUD template/cluster tetap; create/scale/delete hanya mengubah `status` dan `desired` (CREATING, SCALING, DELETING). Endpoint internal `POST /internal/clusters/{id}/ready` (InternalAuth, body: `role`, `node_name`, `kubeconfig_ca`).
3. `k8s-engine-worker` (compute atau controller; pola `orchestration_worker`, polling 5 s, `InternalAuth`): rekonsiliasi.
   - Master: `POST compute /servers` dengan `user_data` yang menjalankan `k3s server --token-file ... --tls-san <ip>`, lalu memanggil callback ready.
   - Worker: dibuat setelah master `ready`, `k3s agent --server https://<master_ip>:6443 --token <join>`.
   - Scale: tambah/hapus VM worker ke `worker_count`.
   - Delete: hapus semua VM cluster; sisa VM dibersihkan pada gagal sebagian.
   - `ACTIVE` bila master ready dan jumlah worker ready sama dengan yang diminta; timeout -> `ERROR` + `status_reason`.
4. Tabel baru `k8s_cluster_nodes` (id, cluster_id, server_id, role, ip, ready) dan kolom pada `k8s_clusters`: `join_token`, `admin_token`, `status_reason`, `master_ip`. Migrasi `k8s_engine_0002`.
5. Kubeconfig: server `https://<master_ip>:6443`, bearer `admin_token`; `insecure-skip-tls-verify` sebagai batasan yang dicatat.

## Uji
- Unit: `user_data` yang dirender (master/agent, tanpa token di log), mesin status, scale up/down, cleanup gagal sebagian, callback internal via `TestClient`. Mutation check per invarian.
- Live (`cloudx-net-ops`): bangun image, create cluster 1 master + 1 worker, tunggu `ACTIVE`, `kubectl get nodes` lewat kubeconfig dari VM/host yang dapat menjangkau, scale, delete, state bersih.

## Keterbatasan
- Tanpa HA control plane, tanpa upgrade versi, tanpa autoscaler.
- Image K3s dibuat manual; versi K3s terkunci ke image.
- Token disimpan plaintext di DB; TLS tanpa verifikasi.
- CSI/CCM yang ada belum disambungkan ke cluster nyata.
- Ukuran lab: 1 master + 1-2 worker kecil (compute 8 GB).
- Bergantung pada VM yang dapat menjangkau API k8s-engine di controller.
