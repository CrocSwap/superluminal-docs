# DFBA Gateway API

This document describes the external API exposed by `dfba_gateway`.

- Public server default bind: `127.0.0.1:8080`
- Admin server default bind: `127.0.0.1:9080`
- WebSocket path default: `/ws`

## Configuration

`dfba_gateway` requires a JSON config file:

```bash
dfba_gateway --config=/opt/aethon/etc/dfba-gateway.json
```

Unknown JSON fields are rejected. Example:

```json
{
  "listen": "0.0.0.0:8080",
  "admin_listen": "127.0.0.1:9080",
  "ws_path": "/ws",
  "ws_allow_no_origin": true,
  "ws_msg_rate_limit_per_min": 3000,
  "ws_msg_rate_limit_burst": 150,
  "sequencer_addr": "sequencer-a:4000",
  "notifier_url": "http://sequencer-a:8081",
  "market_data_scribe_dir": "/var/lib/aethon/scribe_logs/gateway_notifier2_log",
  "market_data_consumer_id": 32006,
  "market_data_fanout_workers": 8,
  "etcd_endpoints": "http://etcd:2379",
  "partition_id": "0x01",
  "signed_msg_max_ttl_seconds": 5,
  "signed_msg_max_future_skew_seconds": 0,
  "rpc_url": "http://host.docker.internal:8899",
  "program_id": "aethxgBbVVUaeoCrcVobrAQcsC6KcNjmtYwyFLGQGn6",
  "gateway_keypair": "/opt/aethon/keys/exchange_authority.json",
  "evm_addresses_path": "/opt/aethon/etc/evm_addresses.json"
}
```

Signed message timing config:

- `signed_msg_max_ttl_seconds`: max effective lifetime from signed `iat`; the only allowed configured value is `5`. Ledger-signed withdraw and set-delegate messages use a fixed `60` second cap.
- `signed_msg_max_future_skew_seconds`: max accepted future offset for signed `iat`; the only allowed value is `0`

## Transport and Limits

### WebSocket

- Max inbound frame size: `256 KiB`
- Max inbound text command size: `64 KiB`
- Max signed proof payload size: `512 bytes`
- Per-connection inbound WebSocket message rate limit, including data frames and ping/pong control frames:
  - default `3000` messages/minute
  - default burst `150` messages
  - max configurable burst `300` messages
  - configurable with `ws_msg_rate_limit_per_min` and `ws_msg_rate_limit_burst`
  - exceeded connections are closed with `msg_rate_limit_exceeded`
  - order-like commands are counted by this same limiter:
    - `signed_order`
    - `signed_cancel`
    - `signed_modify`
  - deposit/withdraw commands are also counted by this same limiter.
  - application-level `ping` messages and WebSocket ping/pong control frames are counted by this same limiter.
  - invalid or unauthenticated messages are counted before command validation so malformed-message floods cannot bypass the limit.
  - Burst is the token bucket capacity: a connection may briefly send up to `ws_msg_rate_limit_burst` messages faster than the per-minute refill rate, then must wait for tokens to refill.
  - Setting `ws_msg_rate_limit_per_min` to `0` disables the limiter. When enabled, `ws_msg_rate_limit_burst` must be greater than `0`.
- Ping/pong:
  - Server sends ping every 20s.
  - Read deadline is 120s and extended on pong.

### Origin policy

- Enforced at WS upgrade.
- `ws_allowed_origins`:
  - Empty: permissive for browser origins.
  - Non-empty: allowlist of exact normalized origins (`scheme://host`).
- `ws_allow_no_origin`:
  - `true` (default): allows clients without `Origin` header (typical API bots).

## HTTP API

### Public server

#### `GET /healthz`

Returns:

```text
ok
```

Status: `200`.

#### `GET /v1/symbology`

Returns a symbology snapshot.

Sample response:

```json
{
  "version": 123,
  "markets": [
    {
      "id": 7,
      "symbol": "BTC",
      "quote": "USDC",
      "px_exponent": -8,
      "size_decimals": 5,
      "status": "active"
    }
  ]
}
```

Each market includes required metadata:

- `quote`
- `px_exponent`
- `size_decimals`

#### `GET /v1/symbology/{id}`

Returns a single market object:

```json
{
  "id": 7,
  "symbol": "BTC",
  "quote": "USDC",
  "px_exponent": -8,
  "size_decimals": 5,
  "status": "active"
}
```

Errors:

- `400 bad symbol id`
- `404 symbol not found`

#### `GET /v1/evm/addresses`

Returns public EVM deployment metadata needed by clients to compute user deposit
addresses and submit EVM token transfers.

Sample response:

```json
{
  "version": 1,
  "collateral": "0x1111111111111111111111111111111111111111",
  "gateway": "0x2222222222222222222222222222222222222222",
  "fee_recipient": "0x3333333333333333333333333333333333333333",
  "user_deposit_init_code_hash": "0x4444444444444444444444444444444444444444444444444444444444444444"
}
```

Fields:

- `version`: response schema version.
- `collateral`: ERC20 collateral token address on the EVM chain.
- `gateway`: deployed `EVMVault` address on the EVM chain.
- `fee_recipient`: EVM fee recipient address.
- `user_deposit_init_code_hash`: CREATE2 init code hash for `UserDeposit`.

Status:

- `200`: EVM address metadata is configured.
- `503`: gateway has no EVM address metadata configured.

#### `POST /info`

Accepts a Hyperliquid-style info request body.

Supported request:

```json
{ "type": "meta" }
```

Returns active symbology as a minimal Hyperliquid-compatible metadata response:

```json
{
  "universe": [
    {
      "name": "BTC",
      "szDecimals": 5
    }
  ]
}
```

Notes:

- Method must be `POST`.
- Only `{"type":"meta"}` is supported.
- Non-empty `dex` is rejected.

## Hyperliquid Compatibility

The gateway exposes a small, public market-data surface intended to be Hyperliquid-compatible.

For lowest latency, clients should prefer the native `subscribe_market` market-data API over
the Hyperliquid compatibility layer. The native stream forwards raw gateway market data without
the extra channel wrapping, filtering, and format conversion used by the HL-compatible channels.

Maker/taker behavior:

- Native `subscribe_market` exposes raw public book events for both `maker` and `taker` books.
- Raw `BookSnapshot`, `BookDelta`, and `stream_reset` book events are tagged with `bookType`.
- Hyperliquid-compatible derived book channels are maker-only:
  - `l2Book` is built from the maker book only.
  - `bbo` is built from the maker book only.
  - `allMids` is computed from maker-book BBOs only.
- If a client needs taker-book data, it must use the native raw `subscribe_market` stream and filter by `bookType: "taker"`.

Supported HL-compatible endpoints:

- `POST /info` with `{"type":"meta"}`
- WebSocket `subscribe` / `unsubscribe` for:
  - `l2Book`
  - `bbo`
  - `trades`
  - `allMids`

This compatibility surface is market-data only. Authentication, private account streaming, and signed order entry remain Aethon-native.

### HL WebSocket subscription ack shape

Successful HL-style `subscribe` commands return:

```json
{
  "channel": "subscriptionResponse",
  "data": {
    "method": "subscribe",
    "subscription": {
      "type": "trades",
      "coin": "BTC"
    }
  }
}
```

Notes:

- The response uses a nested `data.subscription` object.
- The `subscription` object reflects the normalized subscription accepted by the gateway.
- HL-style `unsubscribe` does not emit an ack; the gateway just stops delivering that channel.

#### `OPTIONS /v1/symbology`

#### `OPTIONS /v1/symbology/{id}`

CORS preflight for symbology endpoints only.

- `Access-Control-Allow-Origin: *`
- `Access-Control-Allow-Methods: GET, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type, Accept`

### Admin server

#### `GET /healthz`

Returns `ok\n`.

#### `GET /metrics`

Plain text counters:

```text
active_conns <n>
total_msgs <n>
total_reject <n>
total_forward_errors <n>
total_msg_rate_limited <n>
```

## WebSocket API

Default URL: `ws://<public-host>:<public-port>/ws`

Inbound WS commands use one of two shapes:

- Aethon-native commands with a required `type` field
- Hyperliquid-style market-data commands with `method` and `subscription`
Unknown fields are rejected for command payloads.

### Supported inbound command types

- `subscribe_market`
- Hyperliquid-style `subscribe` / `unsubscribe` for `l2Book`, `bbo`, `trades`, `allMids`
- `auth_wallet`
- `auth_response`
- `subscribe_account`
- `user_deposit_address`
- `signed_create_vault`
- `signed_order`
- `signed_cancel`
- `signed_modify`
- `signed_withdraw`
- `signed_deposit_vault`
- `signed_withdraw_vault`
- `signed_set_delegate`
- `ping`

Binary WS messages are rejected with `binary_message_not_supported`.

### Common outbound message types

- `ack`
- `OrderReject`
- `pong`
- `auth_challenge`
- `auth_expired`
- `session_preempted`
- `CreateVaultStatus`
- `CreateVault`
- `SetDelegateStatus`
- Public market stream events (for example `BookSnapshot`, `BookDelta`, `stream_reset`)
- Hyperliquid-style channel-wrapped public market events (`subscriptionResponse`, `l2Book`, `bbo`, `trades`, `allMids`)
- Private/account events (for example `AccountSnapshot`, `Fill`, `OrderAccepted`, `CancelAck`, `ModifyAck`, `ModifyReject`, `CreateVaultStatus`, `CreateVault`, `SetDelegateStatus`)

Notes:

- `ack`, `OrderReject` include:
  - `gateway_send_unix_ns`
- Matcher-originated streamed events include `matcher_unix_ns`.
- Streamed events routed through gateway include `gateway_send_unix_ns`.
- Private `Fill` events include `execution_mode` with the signed-order values:
  - `0 = NORMAL`
  - `1 = MAINTENANCE_LIQUIDATION`
  - `2 = ADL`
  - `3 = VAULT_LIQUIDATION`

## Auth and Session Rules

### Auth gating

- Unauthenticated clients can only use public market data commands (`subscribe_market`, Hyperliquid-style `l2Book` / `bbo` / `trades` / `allMids` subscribe/unsubscribe, `ping`).
- Private/account actions and signed submit require authentication.

### Auth mode

Each WS connection authenticates with wallet challenge mode: `auth_wallet` -> `auth_response`.

### Exactly 1 active authenticated WS per wallet

This gateway enforces **one active authenticated WS per bound wallet**.

What this means:

- If a new connection authenticates/binds the same wallet, the previous connection is preempted.
- The old connection receives:

```json
{
  "type": "session_preempted",
  "reason": "new_session_authenticated"
}
```

- Then old connection auth state is cleared and socket is disconnected.

Concrete examples:

- API bot has wallet `W` authenticated/bound on WS-1. The same wallet authenticates on WS-2: WS-1 gets `session_preempted` and disconnects.

## Command Reference (with examples)

### 1) `subscribe_market` (public)

Request:

```json
{ "type": "subscribe_market", "symbol": 7 }
```

Fields:

- `type`: literal string `subscribe_market`
- `symbol`: active symbology market id (`uint32` on the wire; for example `7`)

Success:

```json
{ "type": "ack", "status": "accepted" }
```

Failure example:

```json
{ "type": "ack", "status": "rejected", "reason": "invalid_symbol" }
```

Streaming behavior:

- `subscribe_market` is the native public raw market-data stream.
- Raw `BookSnapshot`, `BookDelta`, and `Trade` payloads now include `seq`, which matches the upstream SSE `id`.
- Raw `BookSnapshot` and `BookDelta` payloads on this stream carry `bookType: "maker"` or `bookType: "taker"`.
- Clients should filter book events by `bookType` if they only want one view.
- Raw `Trade` events remain on this stream.
- `stream_reset` payloads for this stream also carry `bookType`, and unfiltered recovery may emit one reset for `maker` and one for `taker`.

Native `BookSnapshot` example:

```json
{
  "type": "BookSnapshot",
  "seq": 101,
  "bookType": "maker",
  "id": 7,
  "symbol": "BTC",
  "book_cursor": 5000,
  "batch_id": 44,
  "term": 2,
  "matcher_unix_ns": 1710000000000000000,
  "depth_cap": 100,
  "bids": [
    { "price": 10001000000, "qty": 500000, "order_count": 1 }
  ],
  "asks": [
    { "price": 10002000000, "qty": 300000, "order_count": 2 }
  ],
  "gateway_send_unix_ns": 1710000000000100000
}
```

Native `BookDelta` example:

```json
{
  "type": "BookDelta",
  "seq": 102,
  "bookType": "maker",
  "id": 7,
  "symbol": "BTC",
  "prev_book_cursor": 5000,
  "book_cursor": 5001,
  "batch_id": 44,
  "term": 2,
  "matcher_unix_ns": 1710000000001000000,
  "depth_cap": 100,
  "mark_price": 10001500000,
  "bids": [
    { "price": 10001000000, "qty": 700000, "order_count": 2 }
  ],
  "asks": [
    { "price": 10002000000, "qty": 0, "order_count": 0 }
  ],
  "gateway_send_unix_ns": 1710000000001100000
}
```

Native `Trade` example:

```json
{
  "type": "Trade",
  "seq": 103,
  "id": 7,
  "symbol": "BTC",
  "batch_id": 44,
  "term": 2,
  "matcher_unix_ns": 1710000000002000000,
  "trade_id": 77,
  "side": "B",
  "price": 10001500000,
  "qty": 200000,
  "hash": "0",
  "buyer": [1, 2, 3, 4],
  "seller": [5, 6, 7, 8],
  "gateway_send_unix_ns": 1710000000002100000
}
```

Notes:

- `price`, `qty`, and `mark_price` are raw integer values interpreted using the market's symbology metadata.
- Fields marked `omitempty` by the gateway encoder, such as `seq`, `batch_id`, `term`, `depth_cap`, and `truncated`, are omitted when they have zero or false values.
- `gateway_send_unix_ns` is added by the gateway immediately before sending the event to the client.

### 1b) Hyperliquid-style `l2Book` subscribe (public)

Request:

```json
{
  "method": "subscribe",
  "subscription": { "type": "l2Book", "coin": "BTC" }
}
```

Supported subscription fields:

- `type`: must be `l2Book`
- `coin`: active market symbol string from symbology, for example `BTC`
- `nSigFigs`: optional, supported values are `2`, `3`, `4`, `5`
- `mantissa`: optional, only valid when `nSigFigs` is `5`; supported values are `2` or `5`

Success response:

```json
{
  "channel": "subscriptionResponse",
  "data": {
    "method": "subscribe",
    "subscription": { "type": "l2Book", "coin": "BTC" }
  }
}
```

Streaming payloads:

```json
{
  "channel": "l2Book",
  "data": {
    "coin": "BTC",
    "levels": [
      [{"px":"100.01","sz":"5","n":1}],
      [{"px":"100.02","sz":"3","n":2}]
    ],
    "time": 1710000000000
  }
}
```

Notes:

- This is additive; it does not replace `subscribe_market`.
- Client-facing `l2Book` payloads follow the Hyperliquid shape.
- `l2Book` is derived from the maker book only; the taker book is not exposed on this HL-compatible channel.
- Internal gateway/notifier routing metadata is not exposed to clients.

### 1c) Hyperliquid-style `l2Book` unsubscribe (public)

Request:

```json
{
  "method": "unsubscribe",
  "subscription": { "type": "l2Book", "coin": "BTC" }
}
```

Behavior:

- No unsubscribe ack is sent.
- The gateway simply stops delivering `l2Book` updates for that subscription.

### 1d) Hyperliquid-style `bbo` subscribe (public)

Request:

```json
{
  "method": "subscribe",
  "subscription": { "type": "bbo", "coin": "BTC" }
}
```

Supported subscription fields:

- `type`: must be `bbo`
- `coin`: active market symbol string from symbology, for example `BTC`
- `dex`, `nSigFigs`, `mantissa`: not supported for `bbo`

Success response:

```json
{
  "channel": "subscriptionResponse",
  "data": {
    "method": "subscribe",
    "subscription": { "type": "bbo", "coin": "BTC" }
  }
}
```

Streaming payloads:

```json
{
  "channel": "bbo",
  "data": {
    "coin": "BTC",
    "time": 1710000000000,
    "bbo": [
      {"px":"100.01","sz":"0.00002","n":3},
      {"px":"100.02","sz":"0.00006","n":7}
    ]
  }
}
```

Notes:

- `bbo` is derived from the maker book only; taker-book top-of-book is not exposed on this HL-compatible channel.

### 1e) Hyperliquid-style `bbo` unsubscribe (public)

Request:

```json
{
  "method": "unsubscribe",
  "subscription": { "type": "bbo", "coin": "BTC" }
}
```

Behavior:

- No unsubscribe ack is sent.
- The gateway simply stops delivering `bbo` updates for that subscription.

### 1f) Hyperliquid-style `trades` subscribe (public)

Request:

```json
{
  "method": "subscribe",
  "subscription": { "type": "trades", "coin": "BTC" }
}
```

Supported subscription fields:

- `type`: must be `trades`
- `coin`: active market symbol string from symbology, for example `BTC`
- `dex`, `nSigFigs`, `mantissa`: not supported for `trades`

Success response:

```json
{
  "channel": "subscriptionResponse",
  "data": {
    "method": "subscribe",
    "subscription": { "type": "trades", "coin": "BTC" }
  }
}
```

Streaming payloads:

```json
{
  "channel": "trades",
  "data": [
    {
      "coin": "BTC",
      "side": "A",
      "px": "100.01",
      "sz": "0.00002",
      "hash": "0",
      "time": 1710000000000,
      "tid": 77,
      "users": ["<buyer_wallet>", "<seller_wallet>"]
    }
  ]
}
```

### 1g) Hyperliquid-style `trades` unsubscribe (public)

Request:

```json
{
  "method": "unsubscribe",
  "subscription": { "type": "trades", "coin": "BTC" }
}
```

Behavior:

- No unsubscribe ack is sent.
- The gateway simply stops delivering `trades` updates for that subscription.

### 1h) Hyperliquid-style `allMids` subscribe (public)

Request:

```json
{
  "method": "subscribe",
  "subscription": { "type": "allMids" }
}
```

Supported subscription fields:

- `type`: must be `allMids`
- `dex`: optional but must be empty if supplied
- `coin`, `nSigFigs`, `mantissa`: not supported for `allMids`

Success response:

```json
{
  "channel": "subscriptionResponse",
  "data": {
    "method": "subscribe",
    "subscription": { "type": "allMids" }
  }
}
```

Streaming payloads:

```json
{
  "channel": "allMids",
  "data": {
    "mids": {
      "BTC": "100.015",
      "ETH": "2500.10"
    }
  }
}
```

Notes:

- `allMids` is computed from maker-book BBO state only.

### 1i) Hyperliquid-style `allMids` unsubscribe (public)

Request:

```json
{
  "method": "unsubscribe",
  "subscription": { "type": "allMids" }
}
```

Behavior:

- No unsubscribe ack is sent.
- The gateway simply stops delivering `allMids` updates for that subscription.

### 2) `auth_wallet` (start wallet challenge)

Request:

```json
{
  "type": "auth_wallet",
  "wallet_pubkey": "<base58>",
  "expiry_seconds": 300
}
```

Fields:

- `type`: literal string `auth_wallet`
- `wallet_pubkey`: required Solana public key in base58
- `expiry_seconds`: required integer; `0` means never expire, otherwise must be `> 60`

Rules:

- `expiry_seconds` is required.
- `expiry_seconds = 0` means the wallet-auth session does not expire by gateway TTL.
- Positive `expiry_seconds` values must be `> 60`.
- `wallet_pubkey` is required.
- This is the only command that issues wallet challenges.

On success:

- `ack accepted`
- `auth_challenge` event:

```json
{
  "type": "auth_challenge",
  "wallet_pubkey": "<wallet>",
  "nonce": "<nonce>",
  "issued_unix_ms": 1710000000000,
  "expires_unix_ms": 1710000060000,
  "session_id": "<gateway_session_id>",
  "message": "AETHON_AUTH_V1|<wallet_pubkey>|<nonce>|<iat>|<exp>|<session_id>"
}
```

### 3) `auth_response` (complete wallet challenge)

Request:

```json
{
  "type": "auth_response",
  "wallet_pubkey": "<optional wallet pubkey>",
  "nonce": "<challenge nonce>",
  "signature_b64": "<ed25519 signature over challenge message bytes>",
  "signed_payload_b64": "<optional Ledger V0 signed payload bytes>",
  "is_delegate": true
}
```

Fields:

- `type`: literal string `auth_response`
- `wallet_pubkey`: optional base58 pubkey; if supplied it must match the challenged wallet
- `nonce`: exact nonce from `auth_challenge`
- `signature_b64`: base64-encoded ed25519 signature over the exact `auth_challenge.message` bytes
- `signed_payload_b64`: optional base64-encoded Solana off-chain V0 signed payload. When present, the Ledger prefix and body are verified and the signature is checked over the full signed payload.
- `is_delegate`: optional boolean; when `true`, the signature must verify with the wallet's current delegate key

Rules:

- Challenge TTL is 60s.
- Challenge is single-use (replay rejected).
- Without `is_delegate`, signature must verify against the challenged wallet pubkey and exact challenge `message`.
- With `is_delegate: true`, gateway looks up the challenged wallet's delegate key from notifier and verifies the same challenge `message` with that delegate key.
- On success, wallet is automatically bound for signed-submit authorization.
- Delegate-authenticated sessions may submit orders, cancels, modifies, and vault operations.
- Collateral withdraw requires owner wallet authentication; delegate-authenticated sessions are rejected for `signed_withdraw`.

Delegate rotation warning:

- Before changing a wallet's delegate key, cancel all open orders for that wallet.
- Existing resting orders are not automatically canceled when the delegate changes.
- Orders signed by a previous delegate may fail settlement after the delegate key is updated.

Success:

```json
{ "type": "ack", "status": "accepted" }
```

### Ledger Hardware Wallet Signing

Ledger Solana hardware wallets sign Solana off-chain message payloads, not the
raw gateway message bytes. Ledger clients should use Solana off-chain message
V0 signing.

V0 signed payload layout:

```text
0xff || "solana offchain" ||
version(1) ||
app_domain(32) ||
format(1) ||
signer_count(1) ||
signer_pubkey(32) ||
message_len_le_u16(2) ||
message_body
```

Gateway Ledger clients use:

```text
version = 0
app_domain = "aethon-gateway" padded with zero bytes to 32 bytes
format = 0
signer_count = 1
signer_pubkey = wallet pubkey bytes
```

The V0 prefix before `message_body` is `85` bytes.

Auth challenge Ledger body:

```text
AETHON_AUTH_V1|<wallet_pubkey>|<nonce>|<iat>|<exp>|<session_id>
```

This is the exact `auth_challenge.message` encoded as UTF-8.

`signed_set_delegate` Ledger body:

```text
base64(owner_pubkey || delegate_key || delegate_nonce_le_u64 || request_id64_le_u64 || iat_le_u64 || exp_le_u64)
```

The base64 wrapper is used because the set-delegate payload contains binary
bytes, while Ledger Solana off-chain V0 message bodies must be valid text.

### 4) `subscribe_account` (private stream)

Request:

```json
{
  "type": "subscribe_account"
}
```

Fields:

- `type`: literal string `subscribe_account`
- No other request fields are accepted.

Behavior:

- Requires authentication.
- Uses the wallet already bound by authentication state.
- Does not mutate authentication state.
- On success, starts private wallet stream and snapshot bootstrap.

Success:

```json
{ "type": "ack", "status": "accepted" }
```

Then private events such as:

```json
{
  "type": "AccountSnapshot",
  "wallet_pubkey": "<wallet>",
  "wallet": [1,2,3,4],
  "last_seq": 12345,
  "subaccounts": [{
    "subaccount_id": 0,
    "collateral_balance": 123456789,
    "positions": [{
      "symbol": 1,
      "net_qty": 10,
      "cost_basis": 1234560,
      "pending_fees": -4
    }],
    "working_orders": [],
    "filled_orders": []
  }],
  "vault_accounts": [{
    "vault_id": 0,
    "operation_subaccount_id": 1,
    "leader_source_subaccount_id": 0,
    "collateral_amount": 100000000000,
    "total_vault_shares": 100000000000,
    "last_seq": 12345
  }],
  "vault_participant_accounts": [{
    "participant_account_pubkey": "<pubkey>",
    "owner_wallet_pubkey": "<wallet>",
    "entry_count": 1,
    "last_seq": 12345,
    "entries": [{
      "vault_account_pubkey": "<pubkey>",
      "shares": 100000000000,
      "remaining_cost_basis": 100000000000,
      "last_seq": 12345
    }]
  }]
}
```

Order lifecycle events delivered on the private stream include
`OrderAccepted`, `OrderReject`, `CancelAck`, `CancelReject`, `ModifyAck`, and
`ModifyReject`. They are authoritative; signed submit commands do not emit an
immediate success ack on sequencer acceptance.

`ModifyAck` example:

```json
{
  "type": "ModifyAck",
  "seq": 12346,
  "global_seq": 12346,
  "batch_id": 88,
  "matcher_unix_ns": 1735689600000000000,
  "symbol": 7,
  "side": "buy",
  "role": "maker",
  "tif": "gtc",
  "order_type": 1,
  "client_instruction_id": 9005,
  "subaccount_id": 7,
  "external_order_id": 123456,
  "client_unix_ns": 1735689599999000000,
  "gateway_recv_unix_ns": 1735689599999500000,
  "sequencer_recv_unix_ns": 1735689599999700000,
  "price": 5000100,
  "qty": 2000,
  "delta_ppm": 25,
  "execution_mode": 0
}
```

`ModifyReject` has the same order identity fields plus `reason`; `price`,
`qty`, and `delta_ppm` are the rejected requested values when present:

```json
{
  "type": "ModifyReject",
  "seq": 12347,
  "global_seq": 12347,
  "batch_id": 88,
  "matcher_unix_ns": 1735689600000000000,
  "symbol": 7,
  "side": "buy",
  "role": "maker",
  "tif": "gtc",
  "order_type": 1,
  "client_instruction_id": 9006,
  "subaccount_id": 7,
  "external_order_id": 123456,
  "reason": 2,
  "price": 5000200,
  "qty": 2000,
  "delta_ppm": 30
}
```

Modify reject reason codes:

- `1 = UnknownOrder`
- `2 = RoleMismatch`
- `3 = IOCNotModifiable`
- `4 = MarketNotModifiable`
- `5 = InvalidQty`
- `6 = InvalidPrice`
- `7 = Full`
- `8 = MissingSymbology`

### 5) `user_deposit_address`

Request:

```json
{
  "type": "user_deposit_address",
  "subaccount_id": 0,
  "client_unix_ns": 1710000000000000000
}
```

Fields:

- `type`: literal string `user_deposit_address`
- `subaccount_id`: target trading subaccount id (`uint64`)
- `client_unix_ns`: optional client timestamp in Unix nanoseconds; gateway receive time is used if omitted or zero

Rules:

- Requires an authenticated wallet-bound session.
- The wallet is the authenticated bound wallet. The request does not accept a wallet override.
- The request does not include amount, token account, deposit address, transaction bytes, or VAA bytes.
- Gateway forwards `wallet`, `subaccount_id`, `client_unix_ns`, and `gateway_recv_unix_ns` to the sequencer as `UserDepositAddress`.
- Keeper consumes the sequenced `UserDepositAddress`, computes the CREATE2 deposit address from EVM config, and watches that EVM address. The actual deposit amount comes later from the EVM broadcast VAA.
- Clients should send this command before transferring tokens to the EVM UserDeposit address for that wallet/subaccount. Under heavy volume, if the user has already transferred tokens but the deposit is not showing up in the perp account, send the same `user_deposit_address` message to the gateway again so the keeper watches that address.

UserDeposit address derivation:

- `wallet`: the authenticated bound wallet as 32 bytes.
- `subaccount_id`: `uint64`, encoded big-endian for the CREATE2 salt input.
- `evm_vault`: deployed `EVMVault` address from `GET /v1/evm/addresses` field `gateway`.
- `userDepositInitCodeHash`: `keccak256(type(UserDeposit).creationCode)`, exposed on-chain as `EVMVault.userDepositInitCodeHash()` and returned by `GET /v1/evm/addresses` field `user_deposit_init_code_hash`.

Formula:

```text
salt = keccak256(wallet || uint64_be(subaccount_id))
user_deposit_address = last20bytes(
  keccak256(0xff || evm_vault || salt || userDepositInitCodeHash)
)
```

Clients should fetch `GET /v1/evm/addresses` from the same gateway they use for
WebSocket commands instead of hardcoding local deployment artifacts.

Success:

```json
{ "type": "ack", "status": "accepted" }
```

Busy:

```json
{ "type": "ack", "status": "busy", "reason": "sequencer_unreachable" }
```

Common rejects:

- `invalid_user_deposit_address`
- `subaccount_id_required`
- `auth_required`
- `invalid_actor_pubkey`

### 9) `signed_create_vault`

Request:

```json
{ "type": "signed_create_vault", "msg_b64": "<base64 signed create-vault proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_create_vault`
- `msg_b64`: base64 of the raw signed create-vault proof bytes

Raw proof definition:

- Wire length: `194` bytes
- Signature domain: `AETHEON_CREATE_VAULT_V1`
- The raw proof contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `source_subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `create_vault` `ContractCreateVaultV1`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractCreateVaultV1` fields:

- `vault_id` `uint32`
- `reserved0` `uint32`
- `operation_subaccount_id` `uint64`
- `requested_vault_collateral` `uint64`
- `vault_creation_fee` `uint64`
- `request_id64` `uint64`

Rules enforced by gateway:

- Requires active wallet-bound authentication.
- Sender in proof is the user wallet. `signer_pubkey` must match the authenticated session signer: wallet for wallet auth, delegate key for delegate auth.
- Authenticated actor must have access to `source_subaccount_id`.
- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `source_subaccount_id` may be `0`
- `operation_subaccount_id` must be `> 0`
- `operation_subaccount_id` must differ from `source_subaccount_id`
- `requested_vault_collateral` must be `> 0`
- `requested_vault_collateral + vault_creation_fee` must not overflow `uint64`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "source_subaccount_id": 0,
  "iat": 1710000000,
  "exp": 1710000005,
  "create_vault": {
    "vault_id": 0,
    "reserved0": 0,
    "operation_subaccount_id": 1,
    "requested_vault_collateral": 1000000,
    "vault_creation_fee": 100000000,
    "request_id64": 9006
  }
}
```

### `signed_set_delegate`

Request:

```json
{ "type": "signed_set_delegate", "msg_b64": "<base64 signed set-delegate proof bytes>" }
```

Raw software-signed proof definition:

- Wire length: `160` bytes
- Payload: `owner_pubkey(32) || delegate_key(32) || delegate_nonce(8) || request_id64(8) || iat(8) || exp(8)`
- Signature: `64` bytes over the 96-byte payload. Signer is `owner_pubkey`.

Raw Ledger-signed proof definition:

- Wire length: `277` bytes
- Signed payload: `85-byte Solana off-chain V0 prefix || base64(payload96)`
- Signature: `64` bytes over the full signed payload
- Ledger prefix signer must be `owner_pubkey`

Rules enforced:

- Requires owner wallet authentication. Delegate-authenticated sessions are rejected.
- `owner_pubkey` must match the authenticated wallet.
- `delegate_nonce` must be greater than `0`; on-chain validation requires it to be exactly the next user delegate nonce.
- Bit 63 of `request_id64` is reserved for system use and must be clear.
- `iat` and `exp` must be greater than `0`; gateway caps effective lifetime to `5` seconds for raw software proofs and `60` seconds for Ledger proofs.
- On-chain validation requires `iat <= exp` and `exp` not to be expired at execution time.
- Settlement emits `SetDelegateStatus` after the on-chain transaction reaches terminal status.

Authoritative result events arrive through the private/account stream.

`SetDelegateStatus` is emitted for terminal settlement status:

```json
{
  "type": "SetDelegateStatus",
  "seq": 123,
  "client_instruction_id": 9006,
  "wallet": "<owner wallet>",
  "delegate_pubkey": "<delegate pubkey>",
  "delegate_nonce": 1,
  "status": 0,
  "error_code": 0,
  "term": 77
}
```

- `status=0` and `error_code=0` means finalized success.
- Nonzero `status` or nonzero `error_code` means settlement failed.
- `status` and `error_code` are always present, including zero-valued success fields.
- Retry statuses are internal and are not forwarded as private events.

`CreateVault` is emitted for non-approved create-vault sequencer/risk decisions:

```json
{
  "type": "CreateVault",
  "seq": 122,
  "client_instruction_id": 9006,
  "subaccount_id": 1,
  "source_subaccount_id": 0,
  "source_subaccount_pubkey": "<source subaccount PDA>",
  "operation_subaccount_id": 1,
  "operation_subaccount_pubkey": "<operation subaccount PDA>",
  "vault_id": 0,
  "vault_account_pubkey": "<vault PDA>",
  "participant_account_pubkey": "<participant PDA>",
  "requested_vault_collateral": 1000000,
  "vault_creation_fee": 100000000,
  "total_debit": 101000000,
  "status": 1,
  "error_code": 0,
  "batch_id": 44,
  "term": 77
}
```

- `status=1` and `error_code=0` means the vault already exists.
- Other non-approved decisions are failures.
- `status` and `error_code` are always present, including zero-valued fields.
- `vault_id` is always present; `0` is a valid vault id.
- Approved decisions are not forwarded as `CreateVault`; clients wait for `CreateVaultStatus`.

### 10) `signed_order`

Request:

```json
{ "type": "signed_order", "msg_b64": "<base64 signed order proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_order`
- `msg_b64`: base64 of the raw signed order proof bytes

Raw proof definition:

- Wire length: `191` bytes
- Signature domain: `AETHEON_ORDER_V1`
- The raw proof is not JSON. It contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `order` `ContractOrderV1`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractOrderV1` fields:

- `symbol` `uint32`
- `price` `uint64`
- `qty` `uint64`
- `tif` `uint8`
- `order_type` `uint8`
- `execution_mode` `uint8`
- `role` `uint8`
- `delta_ppm` `int32`
- `side` `uint8`
- `request_id64` `uint64`

Allowed enum values:

- `tif`: `0 = IOC`, `1 = GTC`
- `order_type`: `0 = LIMIT`, `1 = MARK_TO_MID`, `2 = MARKET`
- `execution_mode`: `0 = NORMAL`, `1 = MAINTENANCE_LIQUIDATION`, `2 = ADL`, `3 = VAULT_LIQUIDATION`
- `role`: `0 = MAKER`, `1 = TAKER`
- `side`: `0 = BUY`, `1 = SELL`

Sequencer and auction timing:

- Taker orders are held in the sequencer delay queue for 300 ms before they are
eligible to enter an auction.
- Maker orders bypass the sequencer delay queue and are eligible for the next
auction.
- The same role-based timing applies to cancels and modifies. Maker cancels and
maker modifies bypass the delay queue; taker cancels and taker modifies are
delayed 300 ms.
- Matcher validates cancel/modify role against the live order role. A taker
cannot bypass the delay queue by marking a cancel or modify as maker.

Price and quantity encoding:

- `price` is a raw `uint64` integer carried in wire format; the market's `px_exponent` is applied to interpret it as a decimal price.
- Human price is `price * 10^(px_exponent)`.
- Example: if `px_exponent = -2` and `price = 10001`, the human price is `100.01`.
- `qty` is a raw `uint64` integer carried in wire format in units of `10^(-size_decimals)` for that market.
- Human quantity is `qty * 10^(-size_decimals)`.
- To encode a human decimal quantity, clients compute `raw_qty = human_qty * 10^(size_decimals)`.
- A human decimal quantity is valid only if it is exactly representable at that market resolution and `raw_qty > 0`.
- This means a client decimal input may have at most `size_decimals` fractional digits.
- Examples:
  - BTC with `size_decimals = 5`: minimum increment is `0.00001`; `10.12345` is valid and encodes as `1012345`; `10.123456` is invalid because it is not exactly representable as `uint64 qty`
  - ETH with `size_decimals = 4`: minimum increment is `0.0001`; `10.1234` is valid and encodes as `101234`; `10.12345` is invalid because it is not exactly representable as `uint64 qty`

Limit-price acceptance rule:

- The gateway validates the `price` field only for `LIMIT` orders.
- The rule is based on `6 - size_decimals`.
- Starting from the scaled decimal price, trailing zeros are stripped first.
- After stripping trailing zeros:
  - remaining decimal places must be `<= 6 - size_decimals`
  - if any decimal places remain, the normalized price must have at most 5 significant figures
- This is the same rule implemented by the gateway validator and mirrored in the API client price-grid helper.

Equivalent intuition:

- More `size_decimals` leaves fewer decimal places available for price.
- When decimal places remain, prices are limited to 5 significant figures after normalization.

Rules enforced by gateway:

- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `symbol` must be active symbology and fit in `uint32`
- `qty` must be `> 0`
- For signed raw integer orders, representable quantity precision is implied by the wire encoding above; clients accepting human decimal qty input must reject values with more than `size_decimals` fractional digits
- `execution_mode` must be `0 = NORMAL` for gateway-submitted signed orders
- `role` must be explicitly provided as `MAKER` or `TAKER`; gateway does not infer it from `order_type` or `tif`
- `LIMIT` orders require `price > 0`
- `LIMIT` orders require the raw `price` to satisfy the gateway's accepted price grid for that symbol (`px_exponent`, `size_decimals`, `6 - size_decimals`, 5-significant-figure rule)
- `MARK_TO_MID` orders require `delta_ppm != 0`
- `MARK_TO_MID` orders do not use the `price` field for gateway validation
- `MARK_TO_MID` orders require a current mark price in gateway for precheck;
the final executable limit is determined by matcher at auction time from Pyth
- `MARK_TO_MID` orders require `abs(delta_ppm) <= 50000`, i.e. at most 5% from mark
- `MARKET` orders require `price = 0` and `tif = IOC`
- `MARKET` orders require `role = TAKER`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.
- `iat` is normalized as client send time; if it looks like Unix seconds it is converted internally to nanoseconds

Mark-to-mid price semantics:

- For `MARK_TO_MID`, clients should treat `price` as ignored and usually send `0`.
- The gateway does not apply the `LIMIT` price-grid rule to `MARK_TO_MID`.
- The executable limit is determined by matcher at auction time from the current
Pyth mark and `delta_ppm`.
- `delta_ppm` is parts per million of that auction-time mark price.
- The raw price delta is `floor(mark_price * abs(delta_ppm) / 1_000_000)`, with a minimum of one raw price unit for nonzero `delta_ppm`.
- Effective limit derivation:
  - buy: `derived_limit = mark_price - raw_delta` when `delta_ppm > 0`, and `mark_price + raw_delta` when `delta_ppm < 0`
  - sell: `derived_limit = mark_price + raw_delta` when `delta_ppm > 0`, and `mark_price - raw_delta` when `delta_ppm < 0`
- Positive `delta_ppm` moves buys below mark and sells above mark.
- Negative `delta_ppm` moves the raw derived buy above mark and the raw derived sell below mark.
- The derived limit is snapped to the market price grid by the matcher without worsening the owner price: buys snap down, sells snap up. A small negative delta may therefore snap back to the current grid level instead of crossing.
- So the symbol's explicit limit-price grid affects `LIMIT` orders directly, while `MARK_TO_MID` is governed by mark availability, `delta_ppm`, the gateway delta cap, and matcher price-grid snapping.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "subaccount_id": 7,
  "iat": 1710000000,
  "exp": 1710000005,
  "order": {
    "symbol": 7,
    "price": 5000000,
    "qty": 1000,
    "tif": 1,
    "order_type": 0,
    "execution_mode": 0,
    "role": 0,
    "delta_ppm": 0,
    "side": 0,
    "request_id64": 9001
  }
}
```

Example meanings:

- Limit maker buy order: `order_type=0`, `role=0`, `side=0`, `tif=1`, `price>0`
- Mark-to-mid taker buy below mark: `order_type=1`, `role=1`, `side=0`, `delta_ppm>0`
- Mark-to-mid maker buy above mark: `order_type=1`, `role=0`, `side=0`, `delta_ppm<0`
- Market taker sell order: `order_type=2`, `role=1`, `side=1`, `tif=0`, `price=0`

### 11) `signed_cancel`

Request:

```json
{ "type": "signed_cancel", "msg_b64": "<base64 signed cancel proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_cancel`
- `msg_b64`: base64 of the raw signed cancel proof bytes

Raw proof definition:

- Wire length: `175` bytes
- Signature domain: `AETHEON_CANCEL_V2`
- The raw proof contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `cancel` `ContractCancelV2`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractCancelV2` fields:

- `external_order_id` `uint64`
- `request_id64` `uint64`
- `symbol` `uint32`
- `role` `uint8`

Rules enforced by gateway:

- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `external_order_id` must be `> 0`
- `symbol` must be active symbology
- `role` must be `0 = MAKER` or `1 = TAKER`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Sequencer and matcher behavior:

- Maker cancels bypass the sequencer delay queue and are eligible for the next
auction.
- Taker cancels are held in the sequencer delay queue for 300 ms before they are
eligible to enter an auction.
- Matcher rejects the cancel if the requested `role` does not match the live
order role.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "subaccount_id": 7,
  "iat": 1710000000,
  "exp": 1710000005,
  "cancel": {
    "external_order_id": 123456,
    "request_id64": 9002,
    "symbol": 7,
    "role": 0
  }
}
```

### 12) `signed_modify`

Request:

```json
{ "type": "signed_modify", "msg_b64": "<base64 signed modify proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_modify`
- `msg_b64`: base64 of the raw signed modify proof bytes

Raw proof definition:

- Wire length: `199` bytes
- Signature domain: `AETHEON_MODIFY_V1`
- The raw proof contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `modify` `ContractModifyV1`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractModifyV1` fields:

- `external_order_id` `uint64`
- `request_id64` `uint64`
- `symbol` `uint32`
- `role` `uint8`
- `side` `uint8`
- `tif` `uint8`
- `order_type` `uint8`
- `execution_mode` `uint8`
- `price` `uint64`
- `qty` `uint64`
- `delta_ppm` `int32`

Rules enforced by gateway:

- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `external_order_id` must be `> 0`
- `qty` must be `> 0`
- `symbol` must be active symbology
- `role` must be `0 = MAKER` or `1 = TAKER`
- `side`, `tif`, `order_type`, and `execution_mode` must be valid enum values
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Rules enforced by matcher:

- The target order must exist and still be live.
- The target order must not be IOC or MARKET.
- The modify `role` must match the original order role.
- The modify `side`, `tif`, and `order_type` must match the original order.
- The modify `qty` must be `<=` the target order's current live remaining quantity. Modify can reduce or keep size unchanged, but cannot increase size. To increase size, cancel the existing order and submit a new order after the cancel is acknowledged.
- Only `price`, `qty`, and `delta_ppm` are changed.
- Maker modifies bypass the sequencer delay queue and are eligible for the next
auction.
- Taker modifies are held in the sequencer delay queue for 300 ms before they
are eligible to enter an auction.
- The role match is enforced by matcher so a taker cannot bypass the delay queue by marking a modify as maker.
- `MARK_TO_MID` modifies use an executable price determined by matcher at
auction time from the current Pyth mark and requested `delta_ppm`; invalid
derived prices are rejected.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "subaccount_id": 7,
  "iat": 1710000000,
  "exp": 1710000005,
  "modify": {
    "external_order_id": 123456,
    "request_id64": 9005,
    "symbol": 7,
    "role": 0,
    "side": 0,
    "tif": 1,
    "order_type": 1,
    "execution_mode": 0,
    "price": 5000100,
    "qty": 2000,
    "delta_ppm": 25
  }
}
```

### 13) `signed_withdraw`

Request:

```json
{ "type": "signed_withdraw", "msg_b64": "<base64 signed withdraw proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_withdraw`
- `msg_b64`: base64 of the raw signed withdraw proof bytes

Raw proof definition:

- Software-signed wire length: `234` bytes
- Software signature domain: `AETHEON_WITHDRAW_V1`
- Ledger V0 wire length: `333` bytes
- Ledger V0 body: canonical base64 of the compact withdraw payload.
- The compact withdraw payload contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `withdraw` `ContractWithdrawV1`
- Software proof appends `signer_pubkey` `[32]byte` and `signature` `[64]byte`.
- Ledger proof is `ledger_signed_payload || signature`, where the signer pubkey is in the Ledger prefix.

Decoded `ContractWithdrawV1` fields:

- `amount` `uint64`
- `owner_wallet` `[32]byte`
- `dest_token_account` `[32]byte`; for EVM withdraw this is a bytes32 EVM address word: 12 leading zero bytes followed by the 20-byte EVM recipient address.
- `request_id64` `uint64`

Rules enforced by gateway:

- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `amount` must be `> 0`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "subaccount_id": 7,
  "iat": 1710000000,
  "exp": 1710000005,
  "withdraw": {
    "amount": 1000000,
    "owner_wallet": "<32-byte wallet pubkey bytes>",
    "dest_token_account": "<bytes32 EVM address word>",
    "request_id64": 9004
  }
}
```

Withdraw destination policy:

- `dest_token_account` must encode the target EVM recipient as `0x000000000000000000000000<20-byte address>`.
- Settlement posts a Wormhole message on SVM; the EVM keeper redeems the resulting VAA on EVMVault to transfer collateral.

### 14) `signed_deposit_vault`

Request:

```json
{ "type": "signed_deposit_vault", "msg_b64": "<base64 signed deposit-vault proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_deposit_vault`
- `msg_b64`: base64 of the raw signed deposit-vault proof bytes

Raw proof definition:

- Wire length: `202` bytes
- Signature domain: `AETHEON_DEPOSIT_VAULT_V1`
- The raw proof contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `source_subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `deposit_vault` `ContractDepositVaultV1`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractDepositVaultV1` fields:

- `vault_account_pubkey` `[32]byte`
- `deposit_amount` `uint64`
- `request_id64` `uint64`

Rules enforced by gateway:

- Requires active wallet-bound authentication.
- Sender in proof is the user wallet. `signer_pubkey` must match the authenticated session signer: wallet for wallet auth, delegate key for delegate auth.
- Authenticated actor must have access to `source_subaccount_id`.
- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `deposit_amount` must be `> 0`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "source_subaccount_id": 0,
  "iat": 1710000000,
  "exp": 1710000005,
  "deposit_vault": {
    "vault_account_pubkey": "<32-byte vault PDA>",
    "deposit_amount": 1000000,
    "request_id64": 9007
  }
}
```

### 15) `signed_withdraw_vault`

Request:

```json
{ "type": "signed_withdraw_vault", "msg_b64": "<base64 signed withdraw-vault proof bytes>" }
```

Envelope fields:

- `type`: literal string `signed_withdraw_vault`
- `msg_b64`: base64 of the raw signed withdraw-vault proof bytes

Raw proof definition:

- Wire length: `202` bytes
- Signature domain: `AETHEON_WITHDRAW_VAULT_V1`
- The raw proof contains:
  - `sender_pubkey` `[32]byte`
  - `partition_id` `u16`
  - `destination_subaccount_id` `uint64`
  - `iat` `uint64`
  - `exp` `uint64`
  - `withdraw_vault` `ContractWithdrawVaultV1`
  - `signer_pubkey` `[32]byte`
  - `signature` `[64]byte`

Decoded `ContractWithdrawVaultV1` fields:

- `vault_account_pubkey` `[32]byte`
- `vault_shares` `uint64`
- `request_id64` `uint64`

Rules enforced by gateway:

- Requires active wallet-bound authentication.
- Sender in proof is the user wallet. `signer_pubkey` must match the authenticated session signer: wallet for wallet auth, delegate key for delegate auth.
- Authenticated actor must have access to `destination_subaccount_id`.
- `request_id64` is passed through unchanged. It is client-defined metadata and
may be a nonce, sequence number, trace ID, `0`, or otherwise unused.
- Bit 63 of `request_id64` is reserved for system use. Public gateway clients
must leave it clear.
- `destination_subaccount_id` may be `0` and must refer to an existing
collateral subaccount owned by the sender.
- `vault_shares` must be `> 0`
- `exp` must be `> 0`. Gateway uses the earlier of signed `exp` and `iat + signed_msg_max_ttl_seconds` (default 5 seconds) for admission. The signed proof bytes are not mutated.

Decoded proof example:

```json
{
  "sender_pubkey": "<32-byte user wallet pubkey bytes>",
  "signer_pubkey": "<32-byte ed25519 pubkey bytes that signed the proof>",
  "partition_id": "0x01",
  "destination_subaccount_id": 1,
  "iat": 1710000000,
  "exp": 1710000005,
  "withdraw_vault": {
    "vault_account_pubkey": "<32-byte vault PDA>",
    "vault_shares": 500000,
    "request_id64": 9008
  }
}
```

Signed submit rules:

- Type-driven parse/validation (`signed_order` uses order parser, `signed_create_vault` uses create-vault parser, etc.).
- Sender in proof is the user wallet. `signer_pubkey` must match the authenticated session signer: wallet for wallet auth, delegate key for delegate auth.
- Client must already be authenticated and wallet-bound.
- Signed submit never emits auth challenge; unauthenticated submit is rejected.
- Delegate rules are enforced based on auth mode and delegate binding validity.

Signed submit responses:

- The gateway does not emit an immediate acceptance receipt for sequencer submit.
- Authoritative order/cancel/modify/vault state changes arrive through private/account stream events such as `OrderAccepted`, `OrderReject`, `CancelAck`, `CancelReject`, `ModifyAck`, `ModifyReject`, `CreateVaultStatus`, and `CreateVault`.
- Local validation and submit transport failures still return `ack` with `rejected` or `busy`:

```json
{
  "type": "ack",
  "status": "rejected",
  "reason": "invalid_symbol",
  "client_instruction_id": 123
}
```

or

```json
{
  "type": "ack",
  "status": "busy",
  "reason": "sequencer_unreachable",
  "client_instruction_id": 123
}
```

### 16) `ping`

Request:

```json
{ "type": "ping", "client_unix_ns": 1735689600000000000 }
```

Fields:

- `type`: literal string `ping`
- `client_unix_ns`: required integer timestamp supplied by the client.
- No other request fields are accepted.

Success:

```json
{ "type": "pong", "client_unix_ns": 1735689600000000000 }
```

RTT usage:

- The gateway echoes `client_unix_ns` unchanged.
- The client should capture a local timestamp immediately before send and another local timestamp when the `pong` is received.
- A simple RTT estimate is `client_recv_unix_ns - pong.client_unix_ns`.
- This measures application-layer round-trip time from the client clock domain. It does not measure one-way client->gateway latency.
- The gateway does not provide a server timestamp for RTT calculation because that would mix clock domains and be sensitive to clock drift.

## Minimal End-to-End Flows

### Flow A: wallet direct signing (no delegate)

1. `auth_wallet` with `wallet_pubkey` + `expiry_seconds`.
2. Receive `auth_challenge`.
3. Sign challenge and send `auth_response`.
4. Before transferring EVM deposit tokens, send `user_deposit_address` so keeper watches the wallet/subaccount EVM deposit address.
5. Send `signed_order` / `signed_cancel` / `signed_modify` / `signed_withdraw` / `signed_create_vault` / `signed_deposit_vault` / `signed_withdraw_vault`.

## Common Rejection Reasons (non-exhaustive)

Routing:

- `invalid_json`
- `unknown_type`
- `text_command_too_large`
- `binary_message_not_supported`
- `invalid_ping`

Invalid command payload (unknown/extra fields or wrong shape):

- `invalid_auth_wallet`
- `invalid_auth_response`
- `invalid_subscribe`
- `invalid_subscribe_market`
- `invalid_user_deposit_address`
- `subaccount_id_required`
- `invalid_signed_json`

Wallet auth:

- `auth_wallet_expiry_seconds_required`
- `auth_wallet_expiry_seconds_must_be_gt_60`
- `auth_wallet_pubkey_required`
- `auth_challenge_required`
- `auth_challenge_expired`
- `auth_challenge_replayed`
- `invalid_auth_signature`

Bind/subscribe:

- `auth_wallet_required`
- `auth_required`

Signed submit:

- `invalid_signed_json`
- `invalid_signed_msg_b64`
- `invalid_signed_envelope`
- `invalid_partition_id`
- `iat must be > 0`
- `exp must be > 0 for signed_order`
- `exp must be > 0 for signed_cancel`
- `exp must be > 0 for signed_modify`
- `exp must be > 0 for signed_withdraw`
- `exp must be > 0 for signed_create_vault`
- `exp must be > 0 for signed_deposit_vault`
- `exp must be > 0 for signed_withdraw_vault`
- `exp must be > 0 for signed_set_delegate`
- `signed_message_iat_in_future`
- `signed_message_expired`
- `invalid_signature`
- `request_id64 high bit is reserved`
- `invalid_contract_order`
- `invalid_contract_cancel`
- `invalid_contract_modify`
- `invalid_contract_withdraw`
- `invalid_contract_create_vault`
- `invalid_contract_deposit_vault`
- `invalid_contract_withdraw_vault`
- `sequencer_unreachable`
