"""Minimal Solana legacy-message construction for signer tests (host side).

Only what the tests need: a System Program transfer, optionally preceded by
AdvanceNonceAccount (durable-nonce transaction), serialized exactly as the
Solana runtime expects (compact-u16 lengths, little-endian instruction data).
"""

import base64
import os
import subprocess
import tempfile

SYSTEM_PROGRAM = bytes(32)                                   # 11111111111111111111111111111111
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return bytes(len(s) - len(s.lstrip("1"))) + raw


def b58encode(b):
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


RECENT_BLOCKHASHES_SYSVAR = b58decode("SysvarRecentB1ockHashes11111111111111111111")


def compact_u16(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def transfer_message(from_pk, to_pk, lamports, recent_blockhash, nonce_account=None):
    """Legacy message: optional AdvanceNonceAccount, then SystemProgram::Transfer."""
    if nonce_account is None:
        keys = [from_pk, to_pk, SYSTEM_PROGRAM]
        header = bytes([1, 0, 1])               # 1 signer; 0 readonly signed; 1 readonly unsigned
        ixs = [(2, [0, 1], (2).to_bytes(4, "little") + lamports.to_bytes(8, "little"))]
    else:
        # writable: from (signer, nonce authority), nonce account, to; readonly: sysvar, program
        keys = [from_pk, nonce_account, to_pk, RECENT_BLOCKHASHES_SYSVAR, SYSTEM_PROGRAM]
        header = bytes([1, 0, 2])
        ixs = [(4, [1, 3, 0], (4).to_bytes(4, "little")),          # AdvanceNonceAccount
               (4, [0, 2], (2).to_bytes(4, "little") + lamports.to_bytes(8, "little"))]
    out = bytearray(header)
    out += compact_u16(len(keys)) + b"".join(keys)
    out += recent_blockhash
    out += compact_u16(len(ixs))
    for prog, accts, data in ixs:
        out += bytes([prog]) + compact_u16(len(accts)) + bytes(accts) + compact_u16(len(data)) + data
    return bytes(out)


def transaction(sig, message):
    """Wire transaction: compact-u16 signature count, signatures, message."""
    return compact_u16(1) + sig + message


def openssl_sign(seed, message):
    """Ed25519 signature from OpenSSL (independent third implementation)."""
    der = bytes.fromhex("302e020100300506032b657004220420") + seed
    with tempfile.TemporaryDirectory() as d:
        key, msg, sig = (os.path.join(d, n) for n in ("k.pem", "m.bin", "s.bin"))
        with open(key, "w") as f:
            f.write("-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----\n")
        with open(msg, "wb") as f:
            f.write(message)
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key, "-rawin", "-in", msg, "-out", sig],
                       check=True, capture_output=True)
        with open(sig, "rb") as f:
            return f.read()


def openssl_verify(pub, message, sig):
    der = bytes.fromhex("302a300506032b6570032100") + pub
    with tempfile.TemporaryDirectory() as d:
        key, msg, sg = (os.path.join(d, n) for n in ("p.pem", "m.bin", "s.bin"))
        with open(key, "w") as f:
            f.write("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PUBLIC KEY-----\n")
        with open(msg, "wb") as f:
            f.write(message)
        with open(sg, "wb") as f:
            f.write(sig)
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", key, "-rawin",
                            "-in", msg, "-sigfile", sg], capture_output=True)
        return r.returncode == 0
