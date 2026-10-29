# CLI user guide

`cloudx` is the command line client for CloudX. It is installed with the `cloudx-cli` package (on its own, on any machine that can reach the auth API) and with every controller. This guide covers logging in, the commands you use most, and one end-to-end example. Every command below is checked against the CLI's own command tree by `scripts/check_doc_commands.py` (it runs in the test suite).

Use `-h` or `--help` on the root command or on any group to see the exact options:

```bash
cloudx --help
cloudx server --help
cloudx server create --help
```

Output is a table by default; `-o json` prints JSON (`cloudx -o json server list`). `-p/--profile` selects another set of credentials.

## 1. Logging in

Interactive login asks for the password, checks it against the auth API, and saves an access key in `~/.cloudx/credentials`:

```bash
cloudx auth login --username admin --auth-url http://<controller>:8005
```

For scripts, environment variables work without a saved login:

```bash
export CLOUDX_AUTH_URL=http://<controller>:8005
export CLOUDX_USERNAME=admin
export CLOUDX_PASSWORD='<password>'
```

If you have an access key (created in the dashboard or with `cloudx auth access-key create`), `cloudx auth configure` stores it, or set `CLOUDX_ACCESS_KEY_ID` and `CLOUDX_SECRET_ACCESS_KEY`.

`cloudx auth login --auth-url` also saves the address in `~/.cloudx/config`, so later commands find the auth API without `CLOUDX_AUTH_URL`; the variable wins when both are set.

After login the CLI learns every service URL from the service catalog. To force one service to another address, set `CLOUDX_<SERVICE>_URL`, for example `CLOUDX_COMPUTE_URL`.

## 2. Command cheat sheet

### Identity (`auth`)

```bash
cloudx auth project list
cloudx auth user list

# Domains: a domain admin manages only its own domain (the system admin creates the domain's projects)
cloudx auth project create web --domain d1
cloudx auth user create alice --email a@example.com --project web --role member
cloudx auth user create bob --email b@example.com --project web --domain-admin

cloudx auth user set alice --password 'NewPassword123!'
cloudx auth access-key create
```

There is no CLI group for quotas; they are managed through the APIs.

### Compute (`server`, `flavor`, `keypair`, `server-group`)

```bash
cloudx flavor list
cloudx keypair create my-key                    # writes the private key to my-key.pem
cloudx image list

cloudx server create web-1 --image ubuntu --flavor small --network app-net --key-name my-key --security-group web-sg
cloudx server list
cloudx server show web-1
cloudx server console web-1                     # serial console, Ctrl+] to leave
cloudx server logs web-1
cloudx server stop web-1
cloudx server start web-1
cloudx server reboot web-1
cloudx server resize web-1 --flavor medium
cloudx server delete web-1
```

Every server boots from a root volume; `--volume-size` sets its size in GB. `cloudx hypervisor list` shows the compute nodes and `cloudx placement provider-list` their capacity.

### Networking (`network`, `subnet`, `router`, `floating-ip`, `security-group`)

```bash
cloudx network create app-net
cloudx subnet create app-subnet --network app-net --cidr 10.10.0.0/24 --gateway 10.10.0.1 --dhcp
cloudx router create app-router
cloudx router add-interface app-router --subnet app-subnet
cloudx router set-gateway app-router public-net

cloudx floating-ip create public-net
cloudx floating-ip associate <floating-ip> --server web-1 --router app-router
cloudx floating-ip disassociate <floating-ip>

cloudx security-group create web-sg -d "web and ssh"
cloudx security-group rule add web-sg --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
cloudx security-group rule add web-sg --direction ingress --protocol tcp --port-range-min 80 --port-range-max 443 --remote-ip 0.0.0.0/0
cloudx security-group show web-sg
```

The external network is created once by the admin: [../SETUP.md](../SETUP.md) section 6.

Connecting networks:

```bash
cloudx vpc-peering create dev-to-prod --requester router-dev --accepter router-prod
cloudx bgp-peer list                            # see bgp-dynamic-routing.md
cloudx vpn ikepolicy create ike-1 --auth-algo sha256 --enc-algo aes-256
cloudx vpn ipsecpolicy create ipsec-1 --auth-algo sha256 --enc-algo aes-256
cloudx vpn connection create branch1 \
    --router app-router \
    --peer-address 198.51.100.20 --peer-id 198.51.100.20 \
    --peer-cidr 10.200.0.0/16 \
    --psk 'a-long-shared-secret' \
    --ikepolicy ike-1 --ipsecpolicy ipsec-1
```

### Block storage (`volume`, `volume-type`, `snapshot`, `backup`)

```bash
cloudx volume-type list
cloudx volume create data-1 --size 20 --type gp2
cloudx volume attach data-1 --server web-1
cloudx volume detach data-1
cloudx volume extend data-1 --size 40
cloudx volume snapshot data-1 --name data-1-snap
cloudx snapshot list
cloudx volume backup data-1 --name data-1-backup   # full backup to object storage

# Asynchronous replication to another storage node
cloudx volume enable-replication data-1 --target storage-02
cloudx volume sync-replication data-1
cloudx volume failover data-1
cloudx volume disable-replication data-1
```

Volume types are created by the admin; see `cloudx volume-type create --help` for the examples.

### Images and objects (`image`, `object-store`)

```bash
cloudx image upload ubuntu --file ./ubuntu.qcow2 --disk-format qcow2
cloudx object-store list
cloudx object-store --help                        # buckets, objects, visibility
```

### Secrets (`secret`, `secret-container`)

```bash
cloudx secret create luks-key --payload 'my-passphrase' --type passphrase
cloudx secret list
cloudx secret show luks-key
cloudx secret-container create cert-bundle --type certificate --secret <secret-id>
```

### Kubernetes (`k8s-template`, `cluster`)

```bash
cloudx k8s-template create k3s-default --image k3s-ubuntu --k8s-version v1.30.2+k3s1 \
    --master-flavor medium --worker-flavor small
cloudx cluster create prod --template <template-id> --network app-net --masters 1 --workers 3
cloudx cluster scale <cluster-id> --workers 5
cloudx cluster kubeconfig <cluster-id> > ~/.kube/config
```

The K3s image has to be built first: [k8s-engine.md](k8s-engine.md).

### Auto-scaling and load balancing (`scaling-group`, `load-balancer`, `stack`)

```bash
cloudx scaling-group create web-asg --image ubuntu --flavor small --network app-net \
    --min-instances 2 --max-instances 10 --target-cpu 75

cloudx load-balancer create web-lb --network app-net --type alb
cloudx load-balancer list
cloudx load-balancer listener --help
cloudx load-balancer pool --help
# L7 (ALB, HTTP/HTTPS listener): a policy is the action, its rules are AND-ed conditions
cloudx load-balancer l7policy create LISTENER --name api --redirect-pool POOL --position 1
cloudx load-balancer l7rule create POLICY --type HEADER --key X-Env --compare-type EQUAL_TO --value prod
```

`cloudx stack --help` shows the orchestration (Heat-like) commands.

### Platform services

```bash
cloudx dns zone create example.com                          # dns.md
cloudx file-store share create data --size 5 --network <share-network-id>   # file-store.md
cloudx database create pg --engine postgresql --backend vm --network app-net --image ubuntu --flavor small --storage block --size 5   # database.md
cloudx container create web --image nginx:1.27 --network app-net --cpu 0.5 --memory 128   # container.md
cloudx queue create jobs                                    # messaging.md
cloudx reservation create weekend --flavor small --amount 3 --start 2026-11-01T08:00 --end 2026-11-03T08:00   # reservation.md
cloudx ha hosts                                             # ha.md
```

### Operations (`telemetry`, `alarm`, `billing`, `event`, `service`)

```bash
cloudx telemetry stats
cloudx alarm list
cloudx billing summary
cloudx event log-list --type instance.created             # the audit trail
cloudx service list                                        # CloudX systemd units on this host
cloudx service status compute-api
cloudx service logs compute-api -f
```

## 3. Example: a web server with storage and a public address

```bash
# Network and firewall
cloudx network create app-net
cloudx subnet create app-subnet --network app-net --cidr 10.10.0.0/24 --gateway 10.10.0.1 --dhcp
cloudx router create app-router
cloudx router add-interface app-router --subnet app-subnet
cloudx router set-gateway app-router public-net
cloudx security-group create web-sg -d "ssh and http"
cloudx security-group rule add web-sg --direction ingress --protocol tcp --port 22 --remote-ip 0.0.0.0/0
cloudx security-group rule add web-sg --direction ingress --protocol tcp --port 80 --remote-ip 0.0.0.0/0

# A VM with a data volume
cloudx keypair create app-key && chmod 600 app-key.pem
cloudx server create app-1 --image ubuntu --flavor small --network app-net --key-name app-key --security-group web-sg
cloudx volume create db-1 --size 50
cloudx volume attach db-1 --server app-1

# A public address
cloudx floating-ip create public-net
cloudx floating-ip associate <floating-ip> --server app-1 --router app-router
ssh -i app-key.pem ubuntu@<floating-ip>
```

A first run with a tiny image on a fresh install is in [quickstart.md](quickstart.md).
