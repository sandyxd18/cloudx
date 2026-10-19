# File Store (setara Manila, NFS)

Share NFS yang di-mount VM lewat jaringan tenant. Setara Manila dalam fitur inti, bukan 1:1 (lihat batasan).

## Komponen

| Unit | Node | Fungsi |
|---|---|---|
| `cloudx-file-store-api` | controller | API (port 8017): share network, share, aturan akses |
| `cloudx-file-store-worker` | storage (`file_store_host`) | LV + ext4 per share, NFS-Ganesha per share network di netns `qfs-<id>` |

Share network = port OVN di subnet tenant. Alamatnya jadi target mount. Trafik VM tidak di-NAT, jadi ACL per VM berlaku. Ganesha hanya NFSv4, tanpa rpcbind, port 2049.

## Konfigurasi (`/etc/cloudx/cloudx.conf`)

`file_store_api_url`, `file_store_host` (sama dengan `node_name` node worker), `file_store_mount_root`. Di node worker, `/etc/cloudx/file-store-worker/file-store-worker.conf` berisi `file_store_api_url` ke controller dan `file_store_host`. Installer memasang `nfs-ganesha` hanya di node itu.

## Pemakaian

```
cloudx file-store network create sn --network <net-id>
cloudx file-store share create data --size 5 --network <sn-id>
cloudx file-store access add <share-id> 10.0.0.5 --level rw
cloudx file-store share extend <share-id> 10
# di VM:
mount -t nfs4 -o vers=4.1 <ip>:/<share-id> /mnt
```

Tanpa aturan akses, tidak ada yang bisa mount. Alamat/CIDR lebih lebar dari /8 ditolak (`0.0.0.0/0` tidak bisa). Root selalu di-squash.

## Terbukti di lab

Ganesha listen di IP port dalam netns tanpa rpcbind; VM di compute node mount dan menulis dengan IP asli; VM tanpa rule ditolak; rule dihapus membuat Clients kosong; extend 1 ke 2 GB saat ter-mount; hapus share dan share network membersihkan LV, mount, netns, port OVS dan port OVN.

## Batasan

Tanpa snapshot, clone, shrink, kuota, SMB/CIFS, replikasi, NFSv3, Kerberos. Satu node worker. Root share mode 0777 (akses dikendalikan ACL IP dan Root_Squash). Restart worker tidak memutus NFS (`KillMode=process`), tetapi restart Ganesha memicu grace period 10 detik. Detail: `docs/releases/v0.8.5.md`.
