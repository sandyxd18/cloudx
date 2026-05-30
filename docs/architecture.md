# Architecture

mini OpenStack lab is a single-node real VM lab. Docker Compose runs the API services and OVN control-plane containers; VM execution happens through host libvirt/QEMU/KVM.

## Components

- `networking-api`: Neutron-like API with simple IPAM. It stores metadata in PostgreSQL and writes logical networking to real OVN Northbound DB.
- `image-api`: Glance-like API. It stores OS image metadata and bytes in file storage or MinIO.
- `compute-api`: Nova-like API. It owns flavors, keypairs, server metadata, and lifecycle orchestration. The only backend is libvirt.
- `compute-worker`: Single-node worker that downloads images, creates qcow2 overlays, builds NoCloud config-drive ISO files, binds tap devices to OVS `br-int`, defines libvirt domains, and starts VMs.
- `block-store-api`: Cinder-like API that creates qcow2/raw volume files and asks compute to attach/detach disks through libvirt.
- `postgres`: Shared database.

## Server Create Flow

1. `compute-api` validates the image through `image-api`.
2. `compute-api` validates the network through `networking-api`.
3. `compute-api` validates the keypair if `key_name` is supplied.
4. `compute-api` validates the flavor and stores a flavor snapshot.
5. `compute-api` creates a port with `device_owner=compute:nova-like`.
6. `compute-api` stores the server with status `BUILD`.
7. `compute-api` calls `compute-worker /servers/{id}/spawn`.
8. Worker provisions and starts the libvirt domain.
9. Server becomes `ACTIVE`, or `ERROR` with `error_message`.

There is no scheduler, placement, multi-node support, or live migration in this phase.
