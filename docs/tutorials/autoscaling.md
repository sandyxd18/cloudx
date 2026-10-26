# Tutorial: auto-scaling a group of VMs on CPU load

You will create a scaling group, start its first VM, load that VM's CPU until the group adds a second VM, then remove the load and watch the group take the extra VM away. About 30 minutes.

> **Status.** Run end to end on 2026-10-26 on the three-node bare-metal lab from [multi-node.md](multi-node.md) (CirrOS, `tiny` flavor, a group of one to two VMs). Two bugs found by this run are fixed in builds after 0.8.6: the CLI's `scale-out`/`scale-in` answered `401` (orchestration did not read `X-Auth-Token`), and the telemetry worker sent no metrics at all (it read the wrong key of the server list and looked up libvirt domains by the wrong name). Use a build that has both fixes.

## How it works

- `orchestration-api` stores the scaling groups. `orchestration-worker` (controller) wakes up every 30 seconds, and for each group takes the latest CPU sample of every VM in it from `telemetry-api`.
- `telemetry-worker` (every compute node) samples each VM's CPU, memory, disk and network every 30 seconds and posts the samples to `telemetry-api`.
- **Scale out** when the average CPU of the group is above `--target-cpu`, one VM at a time, up to `--max-instances`, and not more often than every `--cooldown` seconds.
- **Scale in** when the average is below half of `--target-cpu` and the group has more than `--min-instances` VMs: the newest VM is deleted, not more often than every `--scale-in-cooldown` seconds.

Two things that surprise people:

- A new group is **empty**. `--min-instances` is the floor for scale-in; nothing starts the first VM for you. Start it with `scale-out`. With no VM there is no CPU sample, and the worker does nothing.
- The group's VMs are created with the group's image, flavor and network only: no key pair, no user data, and no security group is named, so the compute API's default applies.

## 1. What has to be running

On the controller, three packages beyond the minimal set of the multi-node tutorial:

```bash
cd ~/debs
sudo apt install ./cloudx-orchestration-api_*.deb ./cloudx-orchestration-worker_*.deb ./cloudx-telemetry-api_*.deb
```

The packaged `/etc/cloudx/orchestration-api/orchestration-api.conf` has its own `database_url` with the default password `cloudx`. If you changed the PostgreSQL password, put the real one there too (database `cloudx_orchestration`) and keep the file `640 root:cloudx`. Then create the new tables, register the two services in the catalog and restart them. In `/etc/cloudx/auth-api/auth-api.conf` add the addresses other nodes can reach:

```ini
orchestration_api_url = http://192.168.15.11:8013
telemetry_api_url = http://192.168.15.11:8010
```

```bash
sudo cloudx-manage db sync
sudo cloudx-manage catalog sync --dry-run      # two services, "~ ... -> http://192.168.15.11:..."
sudo cloudx-manage catalog sync
sudo systemctl restart cloudx-orchestration-api cloudx-orchestration-worker cloudx-telemetry-api
cloudx auth login --username admin --auth-url http://192.168.15.11:8005    # the CLI learns the new services at login
```

On each compute node (the package comes with `cloudx-compute`; on a node trimmed for RAM it may have been disabled, as it was on the lab):

```bash
sudo systemctl enable --now cloudx-telemetry-worker
```

Check that metrics arrive. After a minute, with a VM running:

```console
$ cloudx telemetry stats
| metric_type | instance_id                          | unit |   count |  avg | ...
| cpu_percent | 862aeceb-246c-4d8b-b5dd-a902f2f3d033 | %    |       3 | 0.17 | ...
```

An empty table means the telemetry worker is not sending. Look at `journalctl -u cloudx-telemetry-worker` and make sure the node's `compute_host` in `/etc/cloudx/telemetry-worker/telemetry-worker.conf` is the same name `cloudx hypervisor list` shows.

## 2. Create the group

```bash
cloudx scaling-group create web-asg \
    --image cirros --flavor tiny --network demo-net \
    --min-instances 1 --max-instances 2 \
    --target-cpu 50 --cooldown 60 --scale-in-cooldown 60
```

Note the group `id` it prints (the commands below take the id, not the name). The cooldowns are short so the tutorial moves along; use minutes in real life.

## 3. Start the first VM

```bash
GROUP=<group id>
cloudx scaling-group scale-out $GROUP
sleep 60
cloudx scaling-group instance-list $GROUP
cloudx server list
```

The VM is named `<group>-asg-1`. Wait until its status is `ACTIVE`. `cloudx scaling-group history $GROUP` shows one entry, `scale_out`, reason `Manual scale-out requested`.

## 4. Load the CPU

Log in to the VM and keep its CPU busy. CirrOS has user `cirros` and password `gocubsgo`; the serial console works without a floating IP:

```bash
cloudx server console web-asg-asg-1          # press Ctrl+] to leave
```

Inside the VM start two busy loops:

```sh
dd if=/dev/zero of=/dev/null &
dd if=/dev/zero of=/dev/null &
```

(The lab drove the console from a script on the compute node, with `virsh console`; typing it by hand is the same.)

Within a minute `cloudx telemetry stats` shows that VM at about `100` for `cpu_percent`. The worker needs one more cycle:

```console
$ cloudx scaling-group history $GROUP
|  id | action    | reason                     | server_id    | avg_cpu_before | instance_count_after | created_at   |
|   2 | scale_out | High CPU usage: 100.0%     | e7cb4631-... | 100.0          |                    2 | ...T06:17:19 |
|   1 | scale_out | Manual scale-out requested | ecaa329a-... | None           |                    1 | ...T06:06:35 |

$ cloudx server list | grep asg
| ecaa329a-... | tut-asg-asg-1            | ACTIVE | tiny | 10.10.0.5 |
| e7cb4631-... | tut-asg-asg-1792995438-0 | ACTIVE | tiny | 10.10.0.6 |
```

The second VM is named after the group and a timestamp. The group is now at `--max-instances`; the worker logs `already at max instances (2)` every 30 seconds while the load stays.

## 5. Remove the load

In the VM:

```sh
killall dd
```

The group's average CPU drops under 25 % (half of the target). After the scale-in cooldown the worker deletes the newest VM:

```console
$ cloudx scaling-group history $GROUP
|  3 | scale_in  | Low CPU usage: 0.2%        | e7cb4631-... | 0.1655... | 1 | ...
```

In the lab this took about three minutes after the load stopped (one telemetry cycle, one worker cycle, the cooldown). The group is back at one VM, the floor.

## 6. Manual control and updates

```bash
cloudx scaling-group scale-out $GROUP         # add one VM now (up to max)
cloudx scaling-group scale-in $GROUP          # remove one VM now
cloudx scaling-group update --help            # change min, max, target and the cooldowns
cloudx scaling-group show $GROUP
```

## Clean up

Deleting a group does **not** delete its VMs. Delete the group, then the VMs:

```bash
cloudx scaling-group delete $GROUP
cloudx server list | grep asg                 # delete what is left with `cloudx server delete <name>`
```

## Limits

- Scaling is by average CPU only, and a group changes by one VM at a time.
- The worker looks at the latest sample of each VM, so a short spike can trigger a scale-out; use the cooldown to damp it.
- Group VMs are not attached to a load balancer from the CLI (the API has a pool field; see [../user-guide-cli.md](../user-guide-cli.md) for the load-balancer commands).
- Lab size: the nodes have 3.7 GB RAM, so the group was kept to two `tiny` VMs.
