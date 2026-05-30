# OVN Localnet

Provider networks use OVN localnet wiring.

For `public-net`, networking-api creates:

```bash
ovn-nbctl lsp-add public-net public-net-localnet
ovn-nbctl lsp-set-type public-net-localnet localnet
ovn-nbctl lsp-set-options public-net-localnet network_name=physnet1
```

OVS maps `physnet1` to `br-ex`:

```bash
ovs-vsctl set open . external_ids:ovn-bridge-mappings=physnet1:br-ex
```

The bridge mapping is host/bootstrap configuration. Floating IP allocation only consumes addresses from the provider pool and does not choose NICs.
