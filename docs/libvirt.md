# Libvirt

The supported compute path is host libvirt with `compute-worker` running in Docker Compose.

```bash
sudo scripts/prepare-host-libvirt.sh --create-br-int
docker compose up -d --build
compute host check
```

Required host checks:

```bash
ls -l /dev/kvm
virsh -c qemu:///system list --all
qemu-img --version
cloud-localds --help
ovs-vsctl br-exists br-int
```

Compose uses bind mounts for libvirt-visible storage. These must be host paths, not Docker named volumes, because host libvirt opens the same paths from the domain XML:

```yaml
/var/run/libvirt/libvirt-sock:/var/run/libvirt/libvirt-sock
/dev/kvm:/dev/kvm
/dev/net/tun:/dev/net/tun
/var/run/openvswitch:/var/run/openvswitch
/var/lib/mini-openstack/compute/instances:/var/lib/mini-openstack/compute/instances
/var/lib/mini-openstack/compute/cache:/var/lib/mini-openstack/compute/cache
/var/lib/mini-openstack/volumes:/var/lib/mini-openstack/volumes
```

`compute-worker` uses `network_mode: host` because tap devices must exist in the host network namespace used by host libvirt/QEMU. It also uses `privileged: true` and `NET_ADMIN`/`SYS_ADMIN` for lab ergonomics. Do not treat this as a production deployment model.
