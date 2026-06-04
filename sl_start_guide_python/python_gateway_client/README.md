# DFBA Gateway Python Client SDK

This directory is self-contained. The local Python environment is created under
`.venv`, and pip's cache is kept under `.pip-cache`.

## Client Install

From this directory:

```bash
./scripts/bootstrap_venv.sh
```

That creates `.venv` locally and installs pinned runtime dependencies from PyPI.
It does not install into system Python.

Run the TUI with:

```bash
./.venv/bin/sl-tui --keypair client_kp.json --feed-id 1 --subaccount 1
```

Fund the wallet/subaccount before starting the client. Local Docker deployments
use the EVM deposit flow:

```bash
DEPOSIT_AMOUNT_USDC=100000 ../docker/deploy/init_user.sh client_kp.json,1
```

## Developer Setup

```bash
./scripts/bootstrap_venv.sh --dev
./scripts/run_checks.sh
```

`--dev` installs pinned runtime dependencies plus developer tools such as
`mypy`, `ruff`, and `build`.

## Shipping

Build a zip that contains source and scripts, but not the local `.venv`:

```bash
./scripts/build_sdk_zip.sh
```

The output is written under `sdk-dist/`.

The zip also includes `docs/getting_started_with_python.md` and `docs/api.md`.
