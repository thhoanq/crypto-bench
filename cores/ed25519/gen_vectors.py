#!/usr/bin/env python3
"""Sinh vector ký Ed25519 (RFC 8032, thông điệp 32 byte) cho tb_bench.v:
    python3 gen_vectors.py [N] > vectors.hex
Mỗi vector = 5 dòng hex 256 bit: sk (seed), pk, M, R, S — chuỗi byte theo thứ tự.
Cài đặt Python thuần theo RFC 8032 §6; --check đối chiếu từng chữ ký với OpenSSL.
"""
import hashlib
import random
import subprocess
import sys
import tempfile

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
GY = 4 * pow(5, P - 2, P) % P


def recover_x(y, sign):
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * pow(2, (P - 1) // 4, P) % P
    return P - x if (x & 1) != sign else x


GX = recover_x(GY, 0)
G = (GX, GY, 1, GX * GY % P)


def add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    Dd = 2 * a[2] * b[2] % P
    E, F, Gg, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, Gg * H % P, F * Gg % P, E * H % P)


def mul(s, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = add(q, pt)
        pt = add(pt, pt)
        s >>= 1
    return q


def compress(pt):
    zi = pow(pt[2], P - 2, P)
    x, y = pt[0] * zi % P, pt[1] * zi % P
    return (y | (x & 1) << 255).to_bytes(32, "little")


def keypair(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return a, h[32:], compress(mul(a, G))


def sign(seed, msg):
    a, prefix, pk = keypair(seed)
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % L
    rb = compress(mul(r, G))
    h = int.from_bytes(hashlib.sha512(rb + pk + msg).digest(), "little") % L
    return pk, rb, ((r + h * a) % L).to_bytes(32, "little")


def openssl_sign(seed, msg):
    der = bytes.fromhex("302e020100300506032b657004220420") + seed
    with tempfile.NamedTemporaryFile(suffix=".der") as k, tempfile.NamedTemporaryFile() as m:
        k.write(der); k.flush()
        m.write(msg); m.flush()
        return subprocess.run(["openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER",
                               "-inkey", k.name, "-in", m.name], capture_output=True, check=True).stdout


def main():
    args = [a for a in sys.argv[1:] if a != "--check"]
    n = int(args[0]) if args else 32
    rnd = random.Random(25519)
    for i in range(n):
        seed, msg = rnd.randbytes(32), rnd.randbytes(32)
        pk, r, s = sign(seed, msg)
        if "--check" in sys.argv:
            assert openssl_sign(seed, msg) == r + s, "vector %d lệch OpenSSL" % i
        for v in (seed, pk, msg, r, s):
            print(v.hex())


if __name__ == "__main__":
    main()
