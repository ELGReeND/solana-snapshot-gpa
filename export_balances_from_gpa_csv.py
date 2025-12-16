#!/usr/bin/env python3
"""Export wallet SOL + SPL token balances from solana-snapshot-gpa TSV/CSV.

Defaults:
  input  = matched.csv
  output = balances.csv
  symbols = symbols.csv (address,symbol,decimals,name)

Input must be produced by solana-snapshot-gpa, which outputs:
  pubkey, owner, data_len, lamports, slot, id, offset, write_version, data(base64)
(delimited either by tabs or commas; this script auto-detects).

Output (TSV by default):
  <wallet>\t<symbol_or_name>\t<ui_amount>\t<mint>

Notes:
- SPL token amounts in the snapshot are raw u64 base units. We convert to UI units by
  dividing by mint decimals from symbols.csv (e.g., USDC has decimals=6 so 120 => 0.00012).
- No snapshot.db dependency and no internet download/build logic.
- For SOL we always write a row for every wallet that has *any* balance (SOL>0 or any token>0).
"""

from __future__ import annotations

import argparse
import base64
import csv
import os
import sys
from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple

LAMPORTS_DECIMALS = 9

TOKENKEG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = []
    while n > 0:
        n, r = divmod(n, 58)
        out.append(ALPH[r])
    pad = 0
    for x in b:
        if x == 0:
            pad += 1
        else:
            break
    return ("1" * pad) + "".join(reversed(out or ["1"]))


def u64_le(b: bytes) -> int:
    return int.from_bytes(b, "little")


def fmt_amount(raw: int, decimals: int) -> str:
    if decimals <= 0:
        return str(raw)
    s = str(raw)
    if len(s) <= decimals:
        s = "0" * (decimals - len(s) + 1) + s
    i = len(s) - decimals
    return s[:i] + "." + s[i:]


def fmt_amount_trim(raw: int, decimals: int) -> str:
    """Decimal formatting without trailing zeros (0.000120 -> 0.00012)."""
    s = fmt_amount(raw, decimals)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _b64decode_loose(s: str) -> Optional[bytes]:
    """Decode base64 where padding may be omitted."""
    try:
        s2 = s.strip()
        pad = (-len(s2)) % 4
        if pad:
            s2 += "=" * pad
        return base64.b64decode(s2, validate=False)
    except Exception:
        return None


def sniff_delimiter(first_line: str) -> str:
    # solana-snapshot-gpa часто пишет TSV (\t), но иногда CSV.
    tabs = first_line.count("\t")
    commas = first_line.count(",")
    if tabs >= commas and tabs > 0:
        return "\t"
    if commas > 0:
        return ","
    return "\t"


def load_symbols_csv(path: str) -> Dict[str, Tuple[str, int, str, str]]:
    """Return mapping: mint -> (symbol, decimals, name, display_default).

    Expected columns (with header): address,symbol,decimals,name
    Also supports: mint,symbol,decimals,name
    If missing, returns {} and we fall back to showing mint and raw units.
    """
    out: Dict[str, Tuple[str, int, str, str]] = {}
    if not os.path.exists(path):
        return out

    with open(path, "r", encoding="utf-8", newline="") as f:
        head = f.readline()
        if not head:
            return out
        delim = sniff_delimiter(head)
        f.seek(0)
        r = csv.reader(f, delimiter=delim)

        first = next(r, None)
        if first is None:
            return out

        def is_header(row: Iterable[str]) -> bool:
            row_l = [c.strip().lower() for c in row]
            return ("address" in row_l) or ("mint" in row_l)

        rows_iter = r
        if is_header(first):
            header = [c.strip().lower() for c in first]
            idx_addr = header.index("address") if "address" in header else header.index("mint")
            idx_sym = header.index("symbol") if "symbol" in header else None
            idx_dec = header.index("decimals") if "decimals" in header else None
            idx_name = header.index("name") if "name" in header else None
        else:
            idx_addr, idx_sym, idx_dec, idx_name = 0, 1, 2, 3

            def chain_first():
                yield first
                yield from rows_iter

            rows_iter = chain_first()

        for row in rows_iter:
            if not row:
                continue
            try:
                mint = row[idx_addr].strip()
                if not mint:
                    continue
                sym = row[idx_sym].strip() if idx_sym is not None and idx_sym < len(row) else ""
                name = row[idx_name].strip() if idx_name is not None and idx_name < len(row) else ""
                dec_s = row[idx_dec].strip() if idx_dec is not None and idx_dec < len(row) else ""
                decimals = int(dec_s) if dec_s else 0
                display_default = sym or name or mint
                out[mint] = (sym or mint, decimals, name or sym or mint, display_default)
            except Exception:
                continue
    return out


def export_balances(inp_path: str,
                    out_path: str,
                    symbols_path: str,
                    display_mode: str,
                    out_delim: str) -> None:
    # Input fields from solana-snapshot-gpa:
    # pubkey, owner, data_len, lamports, slot, id, offset, write_version, data(base64)
    wallet_best: Dict[str, Tuple[int, int]] = {}  # wallet_pubkey_str -> (write_version, lamports)
    token_best: Dict[str, Tuple[int, bytes, bytes, int]] = {}  # token_acct_pubkey_str -> (wv, owner_wallet_bytes, mint_bytes, amount_raw)

    symbols = load_symbols_csv(symbols_path)
    if not symbols:
        sys.stderr.write(f"[symbols] WARNING: '{symbols_path}' not found or empty. "
                         f"Tokens will be output as raw amounts and 2nd column will be mint.\n")

    def display_for(mint: str) -> Tuple[str, int]:
        if mint in symbols:
            sym, dec, name, _disp_default = symbols[mint]
            if display_mode == "name":
                return (name or sym or mint, dec)
            return (sym or name or mint, dec)
        return (mint, 0)

    # Parse input (use "-" for stdin)
    inp_fh = sys.stdin if inp_path == "-" else open(inp_path, "r", encoding="utf-8", newline="")
    try:
        first_line = inp_fh.readline()
        if not first_line:
            raise SystemExit("input file is empty")
        in_delim = sniff_delimiter(first_line)
        if inp_path != "-":
            inp_fh.seek(0)
        reader = csv.reader(inp_fh, delimiter=in_delim)

        for row in reader:
            if not row:
                continue
            if row[0].lower() == "pubkey":
                continue
            if len(row) < 9:
                continue

            pubkey = row[0]
            owner_prog = row[1]
            try:
                lamports = int(row[3])
                write_version = int(row[7])
            except Exception:
                continue

            # Token accounts are owned by token programs; wallet/system accounts are everything else.
            if owner_prog == TOKENKEG or owner_prog == TOKEN2022:
                data = _b64decode_loose(row[8])
                if data is None or len(data) < 72:
                    continue
                # SPL token account base layout: mint[0:32], owner[32:64], amount[64:72]
                mint_b = data[0:32]
                owner_b = data[32:64]
                amt = u64_le(data[64:72])
                if amt == 0:
                    continue
                prev = token_best.get(pubkey)
                if prev is None or write_version > prev[0]:
                    token_best[pubkey] = (write_version, owner_b, mint_b, amt)
            else:
                prev = wallet_best.get(pubkey)
                if prev is None or write_version > prev[0]:
                    wallet_best[pubkey] = (write_version, lamports)
    finally:
        if inp_path != "-":
            inp_fh.close()

    # Aggregate token balances by (owner,mint)
    agg: Dict[bytes, int] = defaultdict(int)
    wallets_with_tokens = set()  # owner bytes
    for (_wv, owner_b, mint_b, amt) in token_best.values():
        wallets_with_tokens.add(owner_b)
        agg[owner_b + mint_b] += amt

    wallet_lamports: Dict[str, int] = {}
    wallets_any = set()

    # wallets that have SOL>0
    for wallet, (_wv, lamports) in wallet_best.items():
        wallet_lamports[wallet] = lamports
        if lamports > 0:
            wallets_any.add(wallet)

    # wallets that have any token>0
    for owner_b in wallets_with_tokens:
        wallets_any.add(b58encode(owner_b))

    # Write output (use "-" for stdout)
    out_fh = sys.stdout if out_path == "-" else open(out_path, "w", encoding="utf-8", newline="")
    try:
        writer = csv.writer(out_fh, delimiter=out_delim)

        # SOL rows: for every wallet that has any balance (SOL>0 OR token>0),
        # write SOL (possibly 0) as well.
        for wallet in sorted(wallets_any):
            lamports = wallet_lamports.get(wallet, 0)
            writer.writerow([wallet, "SOL", fmt_amount_trim(lamports, LAMPORTS_DECIMALS), ""])

        # Token rows: <wallet> <symbol_or_name> <ui_amount> <mint>
        for key, raw_amt in agg.items():
            owner_b = key[:32]
            mint_b = key[32:]
            wallet = b58encode(owner_b)
            if wallet not in wallets_any:
                continue
            mint = b58encode(mint_b)
            disp, dec = display_for(mint)
            ui = fmt_amount_trim(raw_amt, dec)
            writer.writerow([wallet, disp, ui, mint])
    finally:
        if out_path != "-":
            out_fh.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", nargs="?", default="matched.csv",
                   help="input matched.csv/tsv from solana-snapshot-gpa (default: matched.csv). Use '-' for stdin.")
    p.add_argument("output", nargs="?", default="balances.csv",
                   help="output balances file (default: balances.csv). Use '-' for stdout.")
    p.add_argument("--symbols", default="symbols.csv",
                   help="symbols CSV with columns: address,symbol,decimals,name (default: symbols.csv)")
    p.add_argument("--display", choices=["symbol", "name"], default="symbol",
                   help="What to print in the 2nd column for tokens (default: symbol)")
    p.add_argument("--out-delim", default="\t",
                   help="Output delimiter (default: tab). Use ',' for CSV.")

    args = p.parse_args(argv)

    export_balances(
        inp_path=args.input,
        out_path=args.output,
        symbols_path=args.symbols,
        display_mode=args.display,
        out_delim=args.out_delim,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
