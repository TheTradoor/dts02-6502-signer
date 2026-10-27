"""Pure-Python RFC 8032 Ed25519 (reference algorithm of Section 10.2).

Written from the RFC's equations with no third-party code so it can serve as
the "independent implementation" for the Section 10.6 acceptance vector.
Not constant time; public test data only.
"""

import hashlib

p = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
d = (-121665 * pow(121666, p - 2, p)) % p
SQRT_M1 = pow(2, (p - 1) // 4, p)


def _recover_x(y, sign):
    if y >= p:
        return None
    x2 = (y * y - 1) * pow(d * y * y + 1, p - 2, p) % p
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p:
        x = x * SQRT_M1 % p
    if (x * x - x2) % p:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


By = 4 * pow(5, p - 2, p) % p
Bx = _recover_x(By, 0)
B = (Bx, By, 1, Bx * By % p)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % p
    Bv = (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C = 2 * P[3] * Q[3] * d % p
    D = 2 * P[2] * Q[2] % p
    E, F, G, H = Bv - A, D - C, D + C, Bv + A
    return (E * F % p, G * H % p, F * G % p, E * H % p)


def _mul(s, P):
    Q = (0, 1, 1, 0)
    while s:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _encode(P):
    zi = pow(P[2], p - 2, p)
    x, y = P[0] * zi % p, P[1] * zi % p
    return (y | (x & 1) << 255).to_bytes(32, "little")


def _decode(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def _eq(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % p == 0


def _h(m):
    return int.from_bytes(hashlib.sha512(m).digest(), "little")


def secret_expand(seed):
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = bytearray(h[:32])
    a[0] &= 248
    a[31] = (a[31] & 63) | 64
    return int.from_bytes(a, "little"), h[32:]


def public_key(seed):
    a, _ = secret_expand(seed)
    return _encode(_mul(a, B))


def sign(seed, msg):
    a, prefix = secret_expand(seed)
    A = _encode(_mul(a, B))
    r = _h(prefix + msg) % L
    R = _encode(_mul(r, B))
    k = _h(R + A + msg) % L
    S = (r + k * a) % L
    return R + S.to_bytes(32, "little")


def verify(pub, msg, sig):
    if len(pub) != 32 or len(sig) != 64:
        return False
    A = _decode(pub)
    R = _decode(sig[:32])
    if A is None or R is None:
        return False
    S = int.from_bytes(sig[32:], "little")
    if S >= L:
        return False
    k = _h(sig[:32] + pub + msg) % L
    return _eq(_mul(S, B), _add(R, _mul(k, A)))
