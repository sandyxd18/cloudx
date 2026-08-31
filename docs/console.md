# Console

Serial console:

```bash
compute server console vm1
```

Exit `virsh console` with `Ctrl+]`.

Console metadata:

```bash
compute server console-url vm1
```

The current milestone exposes virsh console, libvirt VNC inspection, a noVNC WebSocket proxy on port 6080, and an **in-browser VNC console embedded in the CloudX Dashboard**.

### Web Console (Dashboard)

1. Open the CloudX Dashboard at `http://localhost:8080`.
2. Navigate to **Compute ➔ Instances** and click **Connect** on any ACTIVE instance (or navigate to `/console/:id`).
3. An interactive noVNC terminal opens directly inside the AWS Cloudscape console UI.

### Direct noVNC / CLI Access

```bash
cloudx server console-url vm1
# Open websockify proxy at http://localhost:6080
```
