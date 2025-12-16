# Solana Snapshot GPA

`solana-snapshot-gpa` is a Rust command-line tool for extracting account data directly from Solana snapshot archives. It streams a snapshot from disk or over HTTP, applies `getProgramAccounts`-style filters, and emits the matching accounts as CSV so they can be analyzed, deduplicated, or imported into other systems.

## Why this tool?

Solana validators publish snapshot archives (`snapshot-<slot>-<hash>.tar.zst`) that contain every account at a specific slot. The official RPC provides historical account state only while a slot is retained locally, which makes deep retrospection difficult. `solana-snapshot-gpa` bridges that gap by letting you run GPA-like queries against a historical snapshot without needing to spin up a full validator.

Key capabilities:
- Stream snapshots from disk **or directly over HTTP** without storing the whole archive locally.
- Filter by public keys, owners, account size, and memcmp conditions (hex, base58, or a lookup file of 32-byte keys).
- Output append-vec records as CSV (with optional headers) for downstream processing.
- Lightweight binary built on top of [`solana-snapshot-etl`](https://github.com/riptl/solana-snapshot-etl) with minimal dependencies.

## Getting started

### Prerequisites
- Rust toolchain (edition 2021). Install via [rustup](https://rustup.rs/).
- Access to a Solana snapshot archive (`.tar.zst`) or an HTTP URL that serves one.

### Build from source
```bash
git clone https://github.com/ELGReeND/solana-snapshot-gpa
cd solana-snapshot-gpa
cargo +1.85.1 build --release
```

The compiled binary is available at `target/release/solana-snapshot-gpa`.

## CLI usage

```
solana-snapshot-gpa [OPTIONS] <SOURCE>
```

`SOURCE` can be a local snapshot path or an `http(s)://` URL to stream.

### Options
- `-p, --pubkey <PUBKEY>` — Target one or more comma-separated public keys. May be repeated.
- `--pubkeyfile <PATH>` — Read public keys from a file (one per line, blank lines ignored).
- `-o, --owner <OWNER_OPTS>` — Filter by owner program with optional modifiers:
  - `size:<bytes>` — Exact data length.
  - `memcmp:<base58|0xHEX>@<offset>` — Compare bytes at an offset against the provided sequence.
  - `memcmpfile:<path>@<offset>` — Compare 32 bytes at an offset against **any** entry in a file (each line base58 or `0x`-hex, exactly 32 bytes).
- `-n, --noheader` — Suppress CSV header output.

All filters are additive: an account matches if **any** public-key filter matches or **any** owner filter matches. If no filters are provided, every account in the snapshot is emitted.

### Examples

Extract everything from a local snapshot:
```bash
solana-snapshot-gpa snapshot-139240745-XXXX.tar.zst > all.csv
```

Fetch a snapshot over HTTPS and filter by a list of public keys:
```bash
solana-snapshot-gpa --pubkeyfile=pubkeys.txt https://snapshots.solana.com/snapshot.tar.zst > pubkeys.csv
```

Get Token program accounts of length 165 where the mint (offset 32) matches a specific public key:
```bash
solana-snapshot-gpa \
  --owner=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA,size:165,memcmp:r21Gamwd9DtyjHeGywsneoQYR39C1VDwrw7tWxHAwh6@32 \
  snapshot.tar.zst > tokens.csv
```

Match against a set of 32-byte keys stored in a file (one per line):
```bash
solana-snapshot-gpa \
  --owner=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA,memcmpfile:pubkeys.txt@32 \
  snapshot.tar.zst > matched.csv
```

Combine multiple owner filters and public-key filters:
```bash
solana-snapshot-gpa \
  --owner=Prog1111111111111111111111111111111111,size:44,memcmp:0x8000@40 \
  --owner=AnotherOwner1111111111111111111111111111 \
  --pubkey=SomeKey11111111111111111111111111111111,OtherKey22222222222222222222222222222 \
  snapshot.tar.zst > combined.csv
```

### Output format

The tool emits CSV records representing append-vec entries. Columns:
1. `pubkey`
2. `owner`
3. `data_len`
4. `lamports`
5. `slot`
6. `id` (append-vec ID)
7. `offset` (within append-vec)
8. `write_version`
9. `data` (base64-encoded account data)

Use `--noheader` if you prefer the output without the header row.

Because append-vecs contain historical write versions, you may see multiple rows for the same account. The latest entry has the highest `write_version`.

#### Selecting the latest write version
```bash
solana-snapshot-gpa --owner=<...> snapshot.tar.zst > result.csv
# keep the latest write_version per pubkey
tail -n +2 result.csv | sort -t, -k8,8nr | awk -F, '!seen[$1]++' > result.latest.csv
```

#### Preparing data for `solana-test-validator`
```bash
solana-snapshot-gpa --owner=<...> snapshot.tar.zst > result.csv
# latest version per pubkey
tail -n +2 result.csv | sort -t, -k8,8nr | awk -F, '!seen[$1]++' > result.latest.csv
# convert to account JSON
mkdir -p accounts
awk -F, -v out="accounts" '{filename=out"/"$1".json"; print "{\"pubkey\":\""$1"\",\"account\":{\"lamports\":"$4",\"data\":[\""$9"\",\"base64\"],\"owner\":\""$2"\",\"executable\":false,\"rentEpoch\":0}}" > filename; close(filename)}' result.latest.csv
solana-test-validator --account-dir accounts --reset
```

## Example workflow

The repository contains an end-to-end script for Whirlpool accounts at [`example/create-whirlpool-snapshot.sh`](example/create-whirlpool-snapshot.sh). It demonstrates how to:
- Extract all Whirlpool-related accounts.
- Identify position accounts by data length, then fetch only those pubkeys.
- Deduplicate by write version.
- Filter out closed accounts and package the results.

Use it as a reference for building your own pipelines.

## Troubleshooting
- **`Invalid owner filter syntax`** — Ensure the owner public key comes first, followed by comma-separated options (e.g., `OWNER,size:165,memcmp:0x00@0`).
- **`Invalid memcmp file`** — Lines in `memcmpfile` inputs must be 32 bytes (base58 or `0x`-prefixed hex). Blank lines are ignored.
- **`UnexpectedAppendVec` errors** — Verify the snapshot archive is intact and matches the expected Solana version.

## License

Licensed under the [Apache 2.0](LICENSE.md) license. Portions of the snapshot extraction logic are adapted from [`solana-snapshot-etl`](https://github.com/riptl/solana-snapshot-etl).
