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

The current milestone exposes virsh console, libvirt VNC inspection, and a basic noVNC UI on port 6080. `compute server console-url` reports the libvirt VNC port when it can be read from domain XML.


Basic noVNC UI:

```bash
compute server console-url vm1
open http://localhost:6080
```
