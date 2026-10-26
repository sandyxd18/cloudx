# Libvirt

The compute path is host libvirt, with `compute-worker` running as a systemd unit (`cloudx-compute-worker`, as root) on each compute node. The `cloudx-compute` package and `cloudx-setup-compute` set this up (see [deb-install.md](deb-install.md) section 4.2); on a checkout, `scripts/setup-compute-node.sh` does the same.

```bash
sudo scripts/prepare-host-libvirt.sh --create-br-int
sudo systemctl restart cloudx-compute-worker
```

Required host checks:

```bash
ls -l /dev/kvm
virsh -c qemu:///system list --all
qemu-img --version
cloud-localds --help
ovs-vsctl br-exists br-int
```

`compute-worker` runs directly on the host, not in a container, because tap devices must exist in the host network namespace used by libvirt/QEMU, and libvirt must open the same paths that appear in the domain XML. These paths are used by the worker and by libvirt:

```
/var/run/libvirt/libvirt-sock
/dev/kvm
/dev/net/tun
/var/run/openvswitch
/var/lib/cloudx/compute/instances
/var/lib/cloudx/compute/cache
/var/lib/cloudx/volumes
```
