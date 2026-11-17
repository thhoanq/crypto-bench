#!/usr/bin/env python3
"""Mô hình tham chiếu Xoodyak (Xoodoo[12] + Cyclist) viết độc lập với RTL, sinh
vector cho tb_bench.v:  python3 gen_vectors.py > vectors.hex

AEAD (NIST LWC): Cyclist(K, id = nonce); Absorb(AD); C = Encrypt(P); T = Squeeze(16)
Hash:            Cyclist(); Absorb(M); H = Squeeze(32)
Lõi chỉ nhận AD, P, M tối đa 16 byte (một khối) nên vector cũng vậy.

Mỗi vector = 8 dòng hex 128 bit: mode/ad_len/data_len, key, nonce, ad, data_in,
out0, out1, 0.   AEAD: out0 = CT, out1 = tag.   Hash: out0 = H[0:16], out1 = H[16:32].
"""
import random

RC = [0x058, 0x038, 0x3C0, 0x0D0, 0x120, 0x014, 0x060, 0x02C, 0x380, 0x0F0, 0x1A0, 0x012]
M32 = 0xFFFFFFFF


def rol(v, n):
    return ((v << n) | (v >> (32 - n))) & M32


def xoodoo(s):
    a = [int.from_bytes(s[4 * i:4 * i + 4], "little") for i in range(12)]
    for rc in RC:
        p = [a[x] ^ a[4 + x] ^ a[8 + x] for x in range(4)]
        e = [rol(p[(x - 1) % 4], 5) ^ rol(p[(x - 1) % 4], 14) for x in range(4)]
        a = [a[i] ^ e[i % 4] for i in range(12)]                       # theta
        a[4:8] = [a[4 + (x - 1) % 4] for x in range(4)]                # rho west
        a[8:12] = [rol(v, 11) for v in a[8:12]]
        a[0] ^= rc                                                     # iota
        b = [(~a[4 + x] & a[8 + x]) & M32 for x in range(4)] \
            + [(~a[8 + x] & a[x]) & M32 for x in range(4)] \
            + [(~a[x] & a[4 + x]) & M32 for x in range(4)]
        a = [a[i] ^ b[i] for i in range(12)]                           # chi
        a[4:8] = [rol(v, 1) for v in a[4:8]]                           # rho east
        a[8:12] = [rol(a[8 + (x + 2) % 4], 8) for x in range(4)]
    return bytearray(b"".join(v.to_bytes(4, "little") for v in a))


class Cyclist:
    R_HASH, R_KIN, R_KOUT = 16, 44, 24

    def __init__(self, key=b"", ident=b""):
        self.s = bytearray(48)
        self.up_phase = True
        self.keyed = False
        self.r_abs = self.r_sq = self.R_HASH
        if key:
            self.keyed = True
            self.r_abs, self.r_sq = self.R_KIN, self.R_KOUT
            self._absorb_any(key + ident + bytes([len(ident)]), self.r_abs, 0x02)

    def _down(self, x, cd):
        self.up_phase = False
        for i, v in enumerate(bytes(x) + b"\x01"):
            self.s[i] ^= v
        self.s[47] ^= cd if self.keyed else cd & 1

    def _up(self, n, cu):
        self.up_phase = True
        if self.keyed:
            self.s[47] ^= cu
        self.s = xoodoo(self.s)
        return bytes(self.s[:n])

    def _absorb_any(self, x, r, cd):
        blocks = [x[i:i + r] for i in range(0, len(x), r)] or [b""]
        for i, blk in enumerate(blocks):
            if not self.up_phase:
                self._up(0, 0)
            self._down(blk, cd if i == 0 else 0)

    def absorb(self, x):
        self._absorb_any(x, self.r_abs, 0x03)

    def crypt(self, data, decrypt):
        blocks = [data[i:i + self.R_KOUT] for i in range(0, len(data), self.R_KOUT)] or [b""]
        out = b""
        for i, blk in enumerate(blocks):
            ks = self._up(len(blk), 0x80 if i == 0 else 0)
            o = bytes(a ^ b for a, b in zip(blk, ks))
            out += o
            self._down(o if decrypt else blk, 0)
        return out

    def squeeze(self, n):
        y = self._up(min(n, self.r_sq), 0x40)
        while len(y) < n:
            self._down(b"", 0)
            y += self._up(min(n - len(y), self.r_sq), 0)
        return y


def aead_encrypt(key, nonce, ad, pt):
    c = Cyclist(key, nonce)
    c.absorb(ad)
    ct = c.crypt(pt, False)
    return ct, c.squeeze(16)


def xhash(msg):
    c = Cyclist()
    c.absorb(msg)
    return c.squeeze(32)


def pad16(b):
    return (b + bytes(16))[:16].hex()


def main():
    # tự kiểm: KAT hash của NIST LWC (Count = 1, thông điệp rỗng)
    assert xhash(b"").hex().upper() == \
        "EA152F2B47BCE24EFB66C479D4ADF17BD324D806E85FF75EE369EE50DC8F8BD1", xhash(b"").hex()
    rnd = random.Random(2026)
    key = bytes(range(16))
    nonce = bytes(range(16))
    cases = [(key, nonce, bytes(range(16)), bytes(range(16)))]      # khối đầy đủ: lượt "single"
    for adl, ptl in ((0, 0), (0, 16), (16, 0), (1, 1), (5, 13), (15, 15), (3, 4), (7, 8), (16, 12), (16, 2), (16, 16)):
        cases.append((rnd.randbytes(16), rnd.randbytes(16), rnd.randbytes(adl), rnd.randbytes(ptl)))
    for k, n, ad, pt in cases:
        ct, tag = aead_encrypt(k, n, ad, pt)
        print("%032x" % (1 << 16 | len(ad) << 8 | len(pt)))
        for v in (k.hex(), n.hex(), pad16(ad), pad16(pt), pad16(ct), tag.hex(), "0" * 32):
            print(v)
    for ml in (16, 0, 1, 7, 12, 15, 16, 16):
        m = rnd.randbytes(ml)
        h = xhash(m)
        print("%032x" % (3 << 16 | len(m)))
        for v in ("0" * 32, "0" * 32, "0" * 32, pad16(m), h[:16].hex(), h[16:].hex(), "0" * 32):
            print(v)


if __name__ == "__main__":
    main()
