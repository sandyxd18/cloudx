# Tour of the web dashboard

The dashboard is a web console for the same APIs the CLI uses. It is a React app built on the AWS Cloudscape Design System, served by the `cloudx-dashboard` package on port 8080, with live updates over a WebSocket. This page walks through the menu so you know where things are. It is a map, not a click-by-click guide, and it has no screenshots.

> **Status.** Written from the dashboard's source (`services/dashboard/frontend/src`: the menu in `components/Layout.jsx`, the routes in `App.jsx`). On 2026-10-26 the dashboard on the bare-metal lab answered on port 8080 (`CloudX Console`). The pages themselves were not clicked through for this page.

## Opening it

Go to `http://<controller>:8080/` and log in as `admin` with the password from `/etc/cloudx/cloudx.conf` (`sudo grep auth_admin_password /etc/cloudx/cloudx.conf`, see [../../SETUP.md](../../SETUP.md) section 5). A regular user logs in with their own name and password and sees only their project.

If the controller sits behind a firewall that blocks 8080 (the lab's UFW did), use an SSH tunnel instead of opening the port:

```bash
ssh -L 8080:127.0.0.1:8080 <controller>
# then open http://127.0.0.1:8080/
```

## The top bar

- **Project switcher**: the projects your user belongs to.
- **XShell**: a terminal panel inside the console.
- **Light / Dark**: the theme.
- **User menu** (your name): *API access & endpoints*, *Access keys* (the credentials for the CLI), *Role management*, and *Sign out*.

The left menu is the same for everyone, except that *Administration* is only shown to admins. The first page, **Dashboard**, is an overview of the cloud.

## The menu

**Compute**

| Page | What it is for |
|---|---|
| Instances | List and launch VMs. A VM's page has its details, a serial console, and monitoring charts (CPU, memory, disk and network from telemetry) |
| Images | Upload, list and look at images |
| Key pairs | Create or import SSH key pairs |
| Server groups | Affinity and anti-affinity groups ([multi-node.md](multi-node.md) uses them) |
| Scaling groups | Auto-scaling groups ([autoscaling.md](autoscaling.md)) |
| Stacks | Orchestration templates |
| Queues, Reservations | The messaging and reservation services ([../messaging.md](../messaging.md), [../reservation.md](../reservation.md)) |
| XIDE | A list of XIDE workspaces (created and deleted from the page); this tour does not go further into it |

**Containers**: Containers ([../container.md](../container.md)), Databases ([../database.md](../database.md)), High availability ([../ha.md](../ha.md)), Kubernetes Engine ([kubernetes.md](kubernetes.md)).

**Storage**: Volumes (create, attach, extend, detach), Snapshots, Object Store (buckets and objects), File Store (NFS shares, [../file-store.md](../file-store.md)).

**Networking**: Networks (with subnets), Routers, Security groups (the rules per group), Floating IPs, Load Balancers (including L7 policies), DNS ([../dns.md](../dns.md)), VPC Peering, VPN Gateways, BGP Peers, and Topology, a diagram of networks, routers and instances.

**Security**: Key Manager (Vault), the secrets service.

**Observability & Cost**: Telemetry & Metrics, Alarms, Usage & Billing (rating sheets and invoices), and Audit Event Logs.

**Administration** (admins only): Projects & users, Roles, Role assignments, Hypervisors (the compute nodes and their capacity), Flavors, Volume types and Bucket types.

A page only works when its service is installed. On a trimmed install, such as the lab's nine controller services, the pages of services that are not there (Stacks, Scaling groups, Telemetry, Billing and so on) cannot load their data until you install the matching packages ([autoscaling.md](autoscaling.md) shows how to add two of them).

## A first ten minutes

1. **Administration > Hypervisors**: every compute node should be `active`.
2. **Networking > Networks**: the external network and your private network; **Topology** draws them.
3. **Compute > Images** and **Key pairs**: upload an image, create a key pair.
4. **Compute > Instances > Launch**: pick the image, flavor, network, key pair and security group.
5. Open the instance: status, console, and its monitoring charts a minute or two after it starts.
6. **Networking > Floating IPs**: allocate one and associate it with the instance, then connect over SSH.

The same steps with the CLI are in the [quickstart](../quickstart.md).
