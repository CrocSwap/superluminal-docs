# Superluminal Python Client Getting Started

This guide shows how to connect to the Superluminal testnet gateway with the
standalone Python client SDK, fund a subaccount, submit orders, cancel working
orders, receive private order status, and consume native market data.

This guide is intentionally opinionated:

- It uses the `auth_wallet` flow only.
- It uses the native gateway API only.
- It uses the shipped Python `SLPythonClient` TUI as the concrete reference client.
- It does not cover frontend login flows.
- It does not cover Hyperliquid-compatible endpoints.

For full field-level reference, see [api.md](./api.md).

## Testnet Endpoints

- Fogo testnet RPC: `https://testnet.fogo.io`
- Superluminal testnet HTTP: `http://202.8.9.181:8080`
- Superluminal testnet WebSocket: `ws://202.8.9.181:8080/ws`

Important:
- The Python client expects the WebSocket endpoint in `--ws`.
- Use `ws://` unless the deployed gateway explicitly supports `wss://`.

## What You Are Given

You should have received these together:

- this guide
- [api.md](./api.md)
- the `python_gateway_client` SDK directory
- `prefunded_testnet_account.json`

The shipped prefunded wallet for this guide is:

- keypair file: `prefunded_testnet_account.json`
- token account pubkey: `FhmHxRFF9WdcGFXFsRLfJDG2FC5smVCLhDRntwhFCajz`
- the wallet holds funded fake USD in that token account

## Install The Python SDK

The Python SDK is self-contained under `python_gateway_client`. It has no
runtime dependency on the rest of this repository.

Requirements:

- Python 3.12 or newer
- internet access to PyPI for pinned Python dependencies

From the `python_gateway_client` directory:

```bash
./scripts/bootstrap_venv.sh
```

This creates:

```text
python_gateway_client/.venv
```

and installs pinned runtime dependencies into that local virtual environment.
It does not install into system Python.

Runtime dependencies are pinned in `pyproject.toml` and `requirements.txt`:

- `base58`
- `PyNaCl`
- `sortedcontainers`
- `websockets`
- `textual`

## Start The Python TUI

Run the TUI with:

```bash
./.venv/bin/sl-tui \
  --ws ws://202.8.9.181:8080/ws \
  --keypair ./prefunded_testnet_account.json \
  --subaccount 0 \
  --feed-id 1 \
  --feed-id 2 \
  --partition-id 0x01
```

Argument meanings:

- `--ws`: Superluminal testnet gateway WebSocket endpoint
- `--keypair`: wallet keypair JSON file
- `--subaccount`: target trading subaccount
- `--feed-id`: market id to subscribe to at startup; pass it once per market
- `--partition-id`: signed-proof partition id expected by the current deployment

Two practical notes:

- The current default `partition_id` in the Python client is `0x01`.
- The deployed gateway is also configured with `0x01`, so the example command above uses that value explicitly.

## How Superluminal Matches Orders

Superluminal uses a dual-flow batch auction with a 40ms batch cadence.

At a high level:

1. Clients send authenticated commands to the gateway.
2. The gateway forwards accepted commands to the sequencer.
3. The sequencer buffers commands into the current micro-batch.
4. Every 40ms, the batch closes.
5. The matcher processes that closed batch deterministically.
6. The gateway then streams the resulting public market data and private account events back to clients.

This means your client should think in terms of:

- immediate command acceptance or rejection
- followed by asynchronous market and account updates after batch processing

## Maker Book Vs Taker Book

Superluminal exposes two native public book views:

- `bookType: "maker"`
- `bookType: "taker"`

They are two book views carried on the same native `subscribe_market` stream.

Practical meaning:

- Orders carry an explicit `role` of `MAKER` or `TAKER`.
- Native public `BookSnapshot` and `BookDelta` messages include `bookType`.
- Your client can:
  - filter for only `maker`
  - filter for only `taker`
  - or combine both views into one display

The Python client keeps maker and taker book state separately, then combines them
into one ladder view for display. See:

- [book_sync.py](../python_gateway_client/dfba_client/book_sync.py)
- [ingestion.py](../python_gateway_client/dfba_client/ingestion.py)
- [tui/app.py](../python_gateway_client/dfba_client/tui/app.py)

## Mental Model

Use the API in three layers:

1. HTTP for symbology bootstrap.
2. WebSocket for authentication, deposit, orders, cancels, and streaming.
3. Private account events to understand what actually happened to your subaccount.

When using the gateway, distinguish between:

- immediate responses:
  - `ack`
- asynchronous state and lifecycle events:
  - `AccountSnapshot`
  - `OrderAccepted`
  - `Fill`
  - `CancelAck`

For a first-time user, the first collateral deposit is also the effective
account-creation step.

## The Reference Python Client

The shipped Python client implements the full wallet-auth path described in this
guide.

Most relevant files:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [codec.py](../python_gateway_client/dfba_client/codec.py)
- [signer.py](../python_gateway_client/dfba_client/signer.py)
- [account_state.py](../python_gateway_client/dfba_client/account_state.py)
- [tui/app.py](../python_gateway_client/dfba_client/tui/app.py)

The TUI separates socket handling from rendering:

- socket, decode, auth, and command handling run in a worker thread
- Textual renders the TUI in the main thread
- gateway messages are decoded and validated once at the socket boundary
- internal state uses typed Python classes

## Step 1: Load Symbology

Before submitting orders, fetch the active market list:

```bash
curl http://202.8.9.181:8080/v1/symbology
```

Pay attention to:

- `id`
- `symbol`
- `quote`
- `px_exponent`
- `size_decimals`
- `status`

You need these values to:

- choose the correct market id for `subscribe_market`
- encode prices correctly
- encode quantities correctly

The Python client derives the symbology URL from the WebSocket URL and fetches
it automatically on startup. See:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [symbology.py](../python_gateway_client/dfba_client/symbology.py)

## Step 2: Authenticate With `auth_wallet`

This guide uses wallet-direct authentication.

The flow is:

1. Send `auth_wallet`.
2. Receive `auth_challenge`.
3. Sign the challenge message with the wallet in `prefunded_testnet_account.json`.
4. Send `auth_response`.
5. Receive `ack accepted`.

The Python client performs this automatically on connect.

Relevant code:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [commands.py](../python_gateway_client/dfba_client/commands.py)
- [signer.py](../python_gateway_client/dfba_client/signer.py)

## Step 3: Start Private Account Streaming

After successful wallet auth, start the private account stream with:

```json
{ "type": "subscribe_account" }
```

The first important private payload is usually:

- `AccountSnapshot`

This gives the current subaccount view, including:

- positions
- working orders
- account sequence state

After that, the same private stream carries order lifecycle updates such as:

- `OrderAccepted`
- `Fill`
- `CancelAck`

The Python client subscribes automatically after authentication and applies these
updates in:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [account_state.py](../python_gateway_client/dfba_client/account_state.py)

## Step 4: Fund The Account

Fund the wallet/subaccount before starting the Python client. Local Docker
deployments use the EVM deposit flow:

```bash
DEPOSIT_AMOUNT_USDC=100000 docker/deploy/init_user.sh keys/taker.json,0
```

The gateway no longer builds wallet-signed Solana `deposit_collateral`
transactions. After authentication, the Python client subscribes to the account
and market streams; it does not send `deposit_collateral` or
`submit_signed_deposit`.

## Step 5: Subscribe To Native Market Data

Use the native market-data stream:

```json
{ "type": "subscribe_market", "symbol": 1 }
```

Native public events include:

- `BookSnapshot`
- `BookDelta`
- `Trade`
- `stream_reset`

Book events include `bookType`, which is either:

- `maker`
- `taker`

Client guidance:

- filter by `bookType` if you want one view only
- combine both views if you want one merged ladder
- handle `stream_reset` by resetting local state for the affected `bookType`

The Python client subscribes to each configured `--feed-id` and handles maker
and taker books independently before rendering a combined ladder.

## Step 6: Send Your First Order

Orders are sent as signed proofs using:

- `signed_order`

At a minimum, your order logic must choose:

- market id
- side
- quantity
- order type
- time in force
- explicit role: `MAKER` or `TAKER`

Important native rules:

- role is explicit; it is not inferred by the gateway
- market orders require taker role
- limit orders must satisfy the market price grid

The Python TUI exposes these fields in the Order Ticket:

- symbol
- side
- role
- order type
- quantity
- price or mark-to-mid delta, depending on order type

The Python client builds signed order proofs in:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [signer.py](../python_gateway_client/dfba_client/signer.py)

The gateway validation rules are documented in [api.md](./api.md).

## Step 7: Receive Order Status

There are two different kinds of feedback:

Immediate command-path feedback:

- `ack` with `rejected`
- `ack` with `busy`

Asynchronous account-path feedback:

- `OrderAccepted`
- `Fill`
- `CancelAck`
- refreshed `AccountSnapshot`

Interpretation:

- successful signed order/cancel submit does not emit an immediate gateway acceptance message
- `ack rejected` means the command failed local gateway validation
- `ack busy` means a local gateway dependency was temporarily unavailable
- `OrderAccepted`, `Fill`, and `CancelAck` tell you how the subaccount state actually evolved

The Python TUI shows this state in:

- Positions
- Working Orders
- Recent Fills
- Trades
- Log

The Python client applies account updates in:

- [account_state.py](../python_gateway_client/dfba_client/account_state.py)

## Step 8: Cancel An Order

To cancel an existing order in the Python TUI:

1. Wait for the order to appear in the Working Orders panel.
2. Click that row's `Cancel` button.
3. Wait for `CancelAck` or a refreshed account snapshot.

Programmatically, the flow is:

1. Build a signed cancel proof.
2. Send `signed_cancel`.
3. Wait for `CancelAck`.

The Python client implements this flow in:

- [client.py](../python_gateway_client/dfba_client/client.py)
- [signer.py](../python_gateway_client/dfba_client/signer.py)
- [tui/app.py](../python_gateway_client/dfba_client/tui/app.py)

## Common Failure Cases

Watch for these categories of problems:

- invalid market id
- invalid signed payload
- invalid order price grid
- missing or incorrect token account
- missing or zero deposit amount
- market stream reset requiring local book reset and resubscribe logic

Also note:

- the gateway allows only one active authenticated WebSocket per wallet
- if a new authenticated connection binds the same wallet, the older one is preempted
- the Python client logs disconnects and reconnect attempts in the Log panel

## Practical First Run Checklist

1. Confirm the gateway is reachable:

```bash
curl http://202.8.9.181:8080/healthz
```

2. Confirm symbology is available:

```bash
curl http://202.8.9.181:8080/v1/symbology
```

3. Unpack the `python_gateway_client` SDK.
4. Run `./scripts/bootstrap_venv.sh`.
5. Start `./.venv/bin/sl-tui` with the shipped wallet and token account.
6. Let the client complete wallet auth and the initial deposit flow.
7. Verify that you receive:
   - `AccountSnapshot`
   - market data for the configured `--feed-id` values
8. Submit a small test order.
9. Observe private account events such as `OrderAccepted`, `Fill`, and `CancelAck`.
10. If the order is working, cancel it from the Working Orders panel.

## Further Reading

- Full gateway reference: [api.md](./api.md)
- Python SDK:
  - [README.md](../python_gateway_client/README.md)
  - [client.py](../python_gateway_client/dfba_client/client.py)
  - [codec.py](../python_gateway_client/dfba_client/codec.py)
  - [signer.py](../python_gateway_client/dfba_client/signer.py)
  - [tui/app.py](../python_gateway_client/dfba_client/tui/app.py)
