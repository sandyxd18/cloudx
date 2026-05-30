# Compute Keypairs

Keypairs are implemented in `compute-api`. The private key is returned only once by `compute keypair create` and is not stored in the database.

## Commands

```bash
compute keypair create mykey > mykey.pem
chmod 600 mykey.pem
compute keypair import imported --public-key ~/.ssh/id_rsa.pub
compute keypair list
compute keypair show mykey
compute keypair delete mykey
```

When `--key-name` is passed to `compute server create`, compute-api stores the keypair name and public key snapshot. The worker injects that public key into the NoCloud config-drive user-data as `ssh_authorized_keys`.
