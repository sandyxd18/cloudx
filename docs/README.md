# Dokumentasi CloudX

Mulai dari `../README.md` (gambaran umum) dan `../SETUP.md` (instalasi). Dokumen di folder ini dikelompokkan di bawah.

> **Catatan Docker.** CloudX sekarang berjalan native di systemd; berkas `docker-compose*.yml` dan Dockerfile sudah
> dihapus. Beberapa dokumen lama masih memuat perintah `docker compose` dari era sebelumnya: `fase5-production-readiness`
> (17 kali), `troubleshooting` (8), `ovn` (6), dan beberapa lain dengan satu atau dua kali. Untuk langkah terkini
> pakai `../SETUP.md`, `systemd-deployment-guide.md`, dan `multi-node-deployment.md`.

## Memasang dan mengoperasikan
| Dokumen | Isi |
|---|---|
| `../SETUP.md` | Instalasi dari awal (controller, storage node, compute node), akun admin |
| `systemd-deployment-guide.md` | Native systemd dan konfigurasi `/etc/cloudx/` |
| `multi-node-deployment.md` | Deployment multi-node dan pengetesannya |
| `single-node-lab.md` | Lingkup lab single-node |
| `user-guide-cli.md` | Panduan CLI `cloudx` |
| `cleanup.md`, `troubleshooting*.md` | Pembersihan resource dan pemecahan masalah |
| `../services/migrations/README.md` | Migrasi database (Alembic), `make migrate` |

## Arsitektur dan kode
| Dokumen | Isi |
|---|---|
| `architecture.md` | Arsitektur tingkat tinggi |
| `code-structure.md` | Tata letak kode, aturan, tes, penanganan error, eksekusi perintah |
| `feature-comparison.md`, `roadmap-openstack-parity.md` | Perbandingan fitur dan roadmap |

## Compute
`real-vm.md`, `libvirt.md`, `flavor.md`, `keypair.md`, `console.md`, `cloud-init.md`, `placement-lite.md`,
`boot-from-volume.md`, `root-volume.md`

## Jaringan
`ovn.md`, `ovn-dhcp.md`, `ovn-localnet.md`, `ovn-acl.md`, `ovs-ovn-vm-binding.md`, `br-ex.md`, `provider-network.md`,
`multi-network.md`, `ipam.md`, `floating-ip.md`, `security-group.md`, `security-group-ovn-acl.md`,
`metadata-service.md`, `metadata-proxy.md`

## Storage
`storage-backend.md` (image), `snapshot.md` (volume dan backup)

## Per fase
`fase5-production-readiness.md`, `fase5-telemetry-billing-events.md`, `fase6-orchestration-vpc-bgp.md`
