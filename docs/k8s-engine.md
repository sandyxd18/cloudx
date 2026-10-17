# K8s Engine (K3s)

`k8s-engine` membuat cluster K3s sungguhan di VM CloudX. Paritas dengan OpenStack Magnum hanya pada fitur dan perilaku, bukan 1:1.

## Alur

1. `cloudx cluster create` membuat baris cluster berstatus `CREATING` beserta `join_token` dan `admin_token` acak. API hanya mencatat niat.
2. `cloudx-k8s-engine-worker` (controller, polling 5 detik) membuat VM master lewat compute API dengan `user_data` cloud-config.
3. VM master menjalankan `k3s server`, menunggu `/readyz`, lalu memanggil callback `POST /internal/k8s-engine/clusters/{id}/ready` ke controller.
4. Setelah master siap, worker membuat VM worker sebanyak `worker_count`. Tiap worker menjalankan `k3s agent` dan memanggil callback yang sama.
5. Semua node siap: status `ACTIVE`. Lewat `READY_TIMEOUT` (900 detik): status `ERROR` dengan `status_reason`.

Callback dipakai karena controller dan compute tidak punya route ke jaringan tenant. VM harus bisa menjangkau `callback_url`.

## Image K3s

K3s di-bake sekali ke image Glance, sehingga VM tidak butuh internet.

```bash
# di compute node, sebagai root
sudo scripts/build-k3s-image.sh --base-image /var/lib/cloudx/images/ubuntu-24.04.img
sudo scripts/build-k3s-image.sh --base-image ubuntu.img --dry-run   # hanya mencetak langkah
```

Skrip menyalin binary `k3s` dan tarball airgap ke salinan image, lalu mengunggahnya sebagai `k3s-ubuntu`. Image dasar tidak diubah. Versi K3s default `v1.30.2+k3s1`.

## Konfigurasi worker

`/etc/cloudx/k8s-engine-worker/k8s-engine-worker.conf`:

```ini
[DEFAULT]
k8s_engine_api_url = http://127.0.0.1:8015
compute_api_url = http://127.0.0.1:8003
callback_url = http://<ip-controller-yang-terjangkau-vm>:8015
vm_image = k3s-ubuntu
master_flavor = m1.medium
worker_flavor = m1.small
ready_timeout = 900
```

`internal_token` diambil dari `/etc/cloudx/cloudx.conf`.

## Penggunaan

```bash
cloudx k8s-template create tpl-k3s --image k3s-ubuntu
cloudx cluster create demo --template <tpl-id> --network <net-id> --workers 1
cloudx cluster list            # kolom status, master_ip, status_reason
cloudx cluster scale <id> --workers 2
cloudx cluster kubeconfig <id> > kubeconfig
cloudx cluster delete <id>     # status DELETING; worker menghapus semua VM lalu barisnya
```

`scale` hanya diterima saat `ACTIVE` (selain itu 409). `kubeconfig` hanya saat `ACTIVE` atau `SCALING` dan bila `master_ip` sudah ada.

## Diagnosis

- Cluster `CREATING` lama: periksa `cloudx cluster show <id>` (`status_reason`), `journalctl -u cloudx-k8s-engine-worker`, dan apakah VM menjangkau `callback_url`.
- Di VM: `/var/log/cloudx-k3s-boot.log` dan `/var/log/k3s.log`.
- `ERROR`: VM yang tercatat dibersihkan saat cluster dihapus; hapus cluster lalu buat ulang.

## Keterbatasan

- Tanpa HA control plane, tanpa upgrade versi, tanpa autoscaler.
- Image K3s dibuat manual; versi K3s terkunci ke image.
- Token disimpan plaintext di DB; kubeconfig memakai bearer token dengan `insecure-skip-tls-verify`.
- `master_flavor`, `worker_flavor` dan `vm_image` diambil dari konfigurasi worker, bukan dari template cluster.
- VM cluster dibuat lewat panggilan internal sehingga masuk ke project `admin`, bukan project pemilik cluster (kuota dan billing ikut project admin).
- Tidak ada timeout untuk `SCALING` (tidak ada waktu mulai scale yang disimpan).
- Callback `ready` mencocokkan node lewat role dan IP; node pertama yang belum siap dipakai bila IP belum dikenal.
- CSI/CCM yang ada belum disambungkan ke cluster nyata.
- Ukuran lab: 1 master + 1-2 worker kecil (compute 8 GB).
- Bergantung pada VM yang dapat menjangkau API k8s-engine di controller.
