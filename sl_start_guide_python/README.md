# Superluminal Python Starting Guide

This bundle contains the standalone Python gateway client SDK and the gateway
API documentation needed to run it.

Start here:

- `getting_started_with_python.md`
- `api.md`
- `python_gateway_client/README.md`

The Python SDK installs its pinned runtime dependencies into
`python_gateway_client/.venv` using:

```bash
cd python_gateway_client
./scripts/bootstrap_venv.sh
```

Then run:

```bash
./.venv/bin/sl-tui --help
```
