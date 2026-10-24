"""Minimal dependency-free TFRecord writer (tf.train.Example) — avoids installing TensorFlow."""
from __future__ import annotations

import struct


def _crc32c_table():
    poly = 0x82F63B78
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ poly if c & 1 else c >> 1
        t.append(c)
    return t


_T = _crc32c_table()


def crc32c(data: bytes) -> int:
    c = 0xFFFFFFFF
    for b in data:
        c = _T[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def _masked(data: bytes) -> int:
    c = crc32c(data)
    return (((c >> 15) | (c << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(num: int, wire: int, payload: bytes) -> bytes:
    key = _varint((num << 3) | wire)
    return key + (_varint(len(payload)) + payload if wire == 2 else payload)


def _feature(v) -> bytes:
    if v is None:
        return _field(1, 2, b"")  # empty bytes_list
    if isinstance(v, bool) or isinstance(v, int):
        return _field(3, 2, _field(1, 2, _varint(int(v))))  # int64_list{value packed}
    if isinstance(v, float):
        return _field(2, 2, _field(1, 2, struct.pack("<f", v)))  # float_list{value packed}
    if isinstance(v, (list, tuple)):
        if all(isinstance(x, (int, bool)) for x in v):
            return _field(3, 2, _field(1, 2, b"".join(_varint(int(x)) for x in v)))
        if all(isinstance(x, (int, float)) for x in v):
            return _field(2, 2, _field(1, 2, b"".join(struct.pack("<f", float(x)) for x in v)))
        return _field(1, 2, b"".join(_field(1, 2, str(x).encode()) for x in v))
    b = v if isinstance(v, bytes) else str(v).encode()
    return _field(1, 2, _field(1, 2, b))  # bytes_list{value}


def example(row: dict) -> bytes:
    entries = b""
    for k, v in row.items():
        entry = _field(1, 2, k.encode()) + _field(2, 2, _feature(v))
        entries += _field(1, 2, entry)  # Features.feature map entry
    return _field(1, 2, entries)  # Example.features


def write(path, rows) -> int:
    n = 0
    with open(path, "wb") as f:
        for row in rows:
            data = example(row)
            length = struct.pack("<Q", len(data))
            f.write(length + struct.pack("<I", _masked(length)) + data + struct.pack("<I", _masked(data)))
            n += 1
    return n
