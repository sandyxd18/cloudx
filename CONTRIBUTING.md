# Contributing to CloudX

Bug reports, fixes and doc corrections are welcome. For anything bigger than a fix, open an issue first so we can agree
on the shape before you write it.

## Setup

CloudX targets Ubuntu 24.04 and Python 3.12.

```bash
python3.12 -m venv .venv-dev
. .venv-dev/bin/activate
pip install -r requirements-dev.txt
```

## Before you open a pull request

```bash
ruff check services cli tests --select F401,F811,F821,F841 --exclude "node_modules,migrations/versions"
pytest -q
```

CI runs the same two commands (`.github/workflows/tests.yml`) and builds the .deb packages. Unit tests must not need a
running cloud: tests under `tests/live` only run with `pytest --live` against a real lab.

Changes that touch VM, network, storage or packaging behaviour should also be tried on a real node. The scripts
`scripts/test-real-vm.sh`, `scripts/test-floating-ip.sh` and `scripts/test-security-group.sh` cover the common paths. Say in
the pull request what you ran and where.

## Conventions

- One change per pull request, branched from `main`. Name the branch `fix/...`, `feat/...`, `docs/...` or `chore/...`.
- Commit subjects look like `fix(compute): destroy the domain of a server deleted while it is still BUILD`: a type,
  the area in parentheses, then what changed in the imperative. The body says why.
- Add a test with every bug fix. If the bug cannot be reproduced without a lab, describe the lab steps in the pull
  request instead.
- Database changes go through an Alembic migration in the service's `migrations/` directory. Services refuse to start
  against an unmigrated schema, so never edit tables from application code.
- Configuration lives in `/etc/cloudx/cloudx.conf` and `/etc/cloudx/<service>/<service>.conf`, with lowercase keys.
  New keys go into the matching `*.conf.sample` under `etc/cloudx/`.
- Documentation and code comments are in English.
- A new service or unit is declared in `packaging/manifest.toml`; the package build and the systemd units are generated
  from it.

## Building the packages

```bash
make deb          # needs dpkg-deb, fakeroot, python3.12, npm and network access
ls dist/deb/
```

`docs/deb-install.md` describes what each package contains and how the roles map to nodes.

## License

By contributing you agree that your work is released under the MIT license in `LICENSE`.
