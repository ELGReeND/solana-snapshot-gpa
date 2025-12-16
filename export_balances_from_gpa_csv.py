#!/usr/bin/env python3
import base64
import csv
import sys
from collections import defaultdict

LAMPORTS_DECIMALS = 9

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


def fmt_amount(raw: int, decimals: int) -> str:
    if decimals <= 0:
        return str(raw)
    s = str(raw)
    if len(s) <= decimals:
        s = "0" * (decimals - len(s) + 1) + s
    i = len(s) - decimals
    return s[:i] + "." + s[i:]


def u64_le(b: bytes) -> int:
    return int.from_bytes(b, "little")


def main(inp, outp):
    # GPA CSV fields:
    # pubkey, owner, data_len, lamports, slot, id, offset, write_version, data(base64)
    wallet_best = {}  # wallet_pubkey_str -> (write_version, lamports)
    token_best = {}  # token_acct_pubkey_str -> (write_version, owner_wallet_bytes, mint_bytes, amount)

    TOKENKEG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

    reader = csv.reader(inp)
    for row in reader:
        if not row or len(row) < 9:
            continue

        pubkey = row[0]
        owner_prog = row[1]
        lamports = int(row[3])
        write_version = int(row[7])
        data_b64 = row[8]

        if owner_prog == TOKENKEG or owner_prog == TOKEN2022:
            try:
                data = base64.b64decode(data_b64)
            except Exception:
                continue
            if len(data) < 72:
                continue

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

    agg = defaultdict(int)
    wallets_with_tokens = set()
    for (_wv, owner_b, mint_b, amt) in token_best.values():
        wallets_with_tokens.add(owner_b)
        agg[owner_b + mint_b] += amt

    wallets_with_sol = set()
    wallet_lamports = {}
    for wallet, (_wv, lamports) in wallet_best.items():
        wallet_lamports[wallet] = lamports
        if lamports > 0:
            wallets_with_sol.add(wallet)

    wallets_any = set(wallets_with_sol)
    for owner_b in wallets_with_tokens:
        wallets_any.add(b58encode(owner_b))

    writer = csv.writer(outp)
    for wallet in sorted(wallets_any):
        lamports = wallet_lamports.get(wallet, 0)
        writer.writerow([wallet, "SOL", fmt_amount(lamports, LAMPORTS_DECIMALS)])

    for key, amt in agg.items():
        owner_b = key[:32]
        mint_b = key[32:]
        wallet = b58encode(owner_b)
        if wallet not in wallets_any:
            continue
        writer.writerow([wallet, b58encode(mint_b), str(amt)])


if __name__ == "__main__":
    with open(sys.argv[1], "r", encoding="utf-8", newline="") as f_in, \
         open(sys.argv[2], "w", encoding="utf-8", newline="") as f_out:
        main(f_in, f_out)
