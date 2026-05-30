# Real VM Provisioning

`COMPUTE_BACKEND=libvirt` is the default and only supported backend.

Worker provisioning steps:

1. Connect to `LIBVIRT_URI=qemu:///system`.
2. Check `/dev/kvm`.
3. Download image bytes from `image-api /images/{id}/download`.
4. Cache base images in `/var/lib/mini-openstack/compute/cache/images/`.
5. Create `/var/lib/mini-openstack/compute/instances/{server_id}/`.
6. Create `disk.qcow2` overlay and resize it to flavor `disk_gb`.
7. Create NoCloud `seed.iso`.
8. Create `tap-mini-<6-char-server-id>`.
9. Attach the tap to OVS `br-int`.
10. Set OVS Interface external IDs: `iface-id`, `attached-mac`, `vm-id`, and `iface-status=active`.
11. Generate libvirt XML with flavor memory/vCPU.
12. Define and start the VM.

On failure, the worker attempts partial cleanup: domain, tap/OVS port, and instance directory.

Useful commands:

```bash
compute server create vm1 --image cirros --flavor tiny --network private-net --key-name mykey
compute server show vm1
virsh -c qemu:///system list --all
ovs-vsctl show
compute server delete vm1
```
