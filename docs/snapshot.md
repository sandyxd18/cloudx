# Snapshot

Volume snapshot memakai full qcow2 copy. Ini dipilih karena paling stabil dan mudah dibersihkan untuk single-node lab.

Volume snapshot:

```bash
block-store snapshot create vol1 snap1
block-store snapshot show snap1
block-store volume create vol2 --size 10 --snapshot snap1
```

Server snapshot:

```bash
compute server snapshot create vm1 snap-vm1
image show snap-vm1
```

Flow server snapshot:

```text
VM -> Root Volume Snapshot -> Image API
```

Image hasil snapshot bisa dipakai lagi untuk membuat server baru.
