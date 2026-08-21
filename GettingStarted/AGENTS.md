# Superluminal testnet trading-agent contract

This directory is the complete public context for building a Superluminal
testnet trading agent. Do not assume access to another repository, an internal
SDK, a prefunded wallet, or unpublished deployment information.

## Scope and source of truth

- Testnet only: `https://testnet.slx.fi` and `wss://testnet.slx.fi/ws`.
- Read `api.html` for the public HTTP, WebSocket, and signed-wire contracts.
- Read `funding.html` before funding a wallet.
- Use `conformance/test-vectors.json` to verify signing and integer handling.
- Fetch `/v1/symbology` at startup. Do not hardcode market identifiers, price
  exponents, tick sizes, quantity precision, or contract addresses.
- Generate a new local Ed25519 wallet. Never look for or use a bundled keypair.

The first supported implementation should be deliberately narrow: subaccount
`0`, public testnet, one wallet, and price-bounded LIMIT/IOC orders. Do not add
production endpoints, leverage automation, withdrawals, vaults, or delegation
until the basic lifecycle below passes end to end.

## Connection architecture

- A wallet may have exactly one authenticated WebSocket session.
- A TUI, auditor, funding process, or second bot authenticated with the same
  wallet can preempt the active bot with `session_preempted`.
- Use one authenticated socket per wallet for private account state and signed
  commands.
- Public market data may use a separate unauthenticated socket and may be shared
  by multiple wallets.
- Treat `session_preempted` as terminal. Do not reconnect in a loop while
  another process owns the wallet session.

## Create and fund the account

Funding is a multi-system workflow, not a successful faucet HTTP call.

1. Generate and securely store a Solana-format Ed25519 keypair.
2. Open the funding WebSocket and complete `auth_wallet` / `auth_response`.
3. Before `subscribe_account`, send `user_deposit_address` for subaccount `0`.
4. Treat either of these live-compatible responses as registration success:
   - `{ "type": "ack", "status": "accepted" }` received after the registration
     command in this dedicated pre-account-stream phase
   - the matching successful `UserDepositAddressStatus`
5. Derive the CREATE2 address from `GET /v1/evm/addresses`. Never hardcode it.
6. Allow registration to become externally visible before requesting funds.
   Poll or retry with bounded backoff; registration can take tens of seconds.
7. Request the public testnet faucet mint and confirm the Arbitrum Sepolia EVM
   receipt.
8. Close the funding session, open a fresh authenticated account session, send
   `subscribe_account`, and wait for `AccountSnapshot` or
   `UserCollateralUpdate` to prove trading collateral was credited.

The generic accepted acknowledgement is not correlated, which is why
registration must run as a dedicated phase before account subscription. An EVM
receipt is not proof of credited trading collateral. Before repeating a
faucet request after a timeout, recheck the EVM receipt and fresh account state
so the agent does not duplicate an in-flight funding attempt. Fund multiple
wallets serially.

## Readiness gate

Authentication acknowledgement is not readiness. Do not submit an order until
all of the following are true:

- a current `AccountSnapshot` has been received;
- the intended subaccount exists;
- collateral is sufficient for the bounded test order;
- current positions match the expected startup state;
- working orders have been reconciled; and
- market data is synchronized after any `stream_reset`.

On every startup or reconnect, rebuild state from the latest account snapshot
and private lifecycle events before deciding whether a command needs retrying.

## Safe order behaviour

- The acceptance implementation must use an explicitly price-bounded LIMIT or
  IOC order with a small testnet notional.
- Role is explicit: `MAKER` or `TAKER`; it is not inferred.
- Encode price and quantity from the current symbology response.
- `MARKET` with wire fields `price = 0` and `delta_ppm = 0` is unrestricted.
  A configuration value named "slippage" does not protect an order unless the
  signed wire proof contains the documented nonzero reference `price` and
  `delta_ppm`. Do not use unrestricted MARKET in the acceptance flow.
- Sending `signed_order`, `signed_cancel`, or `signed_modify` is not order
  truth. Correlate the private stream by `client_instruction_id` and consume
  `OrderAccepted` or `OrderReject` before advancing state.
- One IOC may produce multiple partial `Fill` events.
- A `Fill` is execution information, not necessarily final settlement. Process
  `FillSettled` and `FillBusted`, and do not treat provisional fills as
  irrevocable accounting.
- Deduplicate fills by wallet public key plus `trade_id`.

## Identifier handling

Gateway identifiers may be JSON numbers or decimal strings. Preserve their
full unsigned 64-bit value. JavaScript and TypeScript implementations must use
`bigint` or lossless decimal strings, never `Number`, for gateway IDs, sequence
numbers, timestamps, and signed `uint64` fields.

Bit 63 of public `request_id64` is reserved, so its maximum public value is
`9223372036854775807`. Other gateway identifiers may use the complete `uint64`
range through `18446744073709551615`.

## Always-on operation

- Persist enough lifecycle state to reconcile after restart.
- Maintain heartbeat and reconnect health.
- Cancel working orders that exceed their intended lifetime.
- Never blindly retry an order after an ambiguous disconnect; reconcile first.
- On SIGTERM, stop creating orders, cancel working orders, flatten any position
  with a bounded order, wait for authoritative lifecycle/settlement events,
  and verify the final account state.

## Acceptance test

The build is complete only when a fresh wallet can perform this sequence using
only this directory:

1. Register the deposit address before account subscription.
2. Accept either supported registration-success response.
3. Wait for registration readiness, request faucet funds, confirm the EVM
   receipt, and confirm credited trading collateral.
4. Connect the bot, receive and validate `AccountSnapshot`, and synchronize
   market data.
5. Place one small price-bounded testnet order and observe the authoritative
   lifecycle, including partial and settlement events when present.
6. Cancel or flatten, close gracefully, reconnect, and prove there are no
   unexpected positions or working orders.
7. Verify that a second authenticated session for the same wallet preempts the
   first and that the bot stops rather than fighting for the session.

Do not include real keys, funded fixture wallets, private endpoints, internal
hostnames, or implementation details in the generated bot or its documentation.
