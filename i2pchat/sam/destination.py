from __future__ import annotations

from base64 import b32encode, b64decode, b64encode
from dataclasses import dataclass
from hashlib import sha256
import struct


I2P_B64_ALTCHARS = b"-~"


def i2p_b64encode(value: bytes) -> str:
    return b64encode(value, altchars=I2P_B64_ALTCHARS).decode("ascii")


def i2p_b64decode(value: str) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Destination is empty")
    return b64decode(text.encode("ascii"), altchars=I2P_B64_ALTCHARS, validate=True)


@dataclass(frozen=True, slots=True)
class PrivateKey:
    data: bytes
    base64: str

    @classmethod
    def from_value(cls, value: bytes | str) -> "PrivateKey":
        if isinstance(value, bytes):
            return cls(data=value, base64=i2p_b64encode(value))
        return cls(data=i2p_b64decode(value), base64=str(value).strip())


class Destination:
    """Internal I2P destination value object.

    This intentionally mirrors the small destination surface historically used by
    I2PChat call sites:

    - `.data`
    - `.base64`
    - `.base32`
    - `.private_key`
    - `Destination(data, has_private_key=True)`
    """

    ECDSA_SHA256_P256 = 1
    ECDSA_SHA384_P384 = 2
    ECDSA_SHA512_P521 = 3
    EdDSA_SHA512_Ed25519 = 7

    default_sig_type = EdDSA_SHA512_Ed25519

    def __init__(
        self,
        data: bytes | str | None = None,
        path: str | None = None,
        has_private_key: bool = False,
    ) -> None:
        if path:
            with open(path, "rb") as handle:
                data = handle.read()

        if data is None:
            raise ValueError("Can't create a destination with no data")

        self.private_key: PrivateKey | None = None
        public_data = data
        if has_private_key:
            self.private_key = PrivateKey.from_value(data)
            cert_len = self._read_cert_len(self.private_key.data)
            public_data = self.private_key.data[: 387 + cert_len]

        if isinstance(public_data, bytes):
            self.data = public_data
            self.base64 = i2p_b64encode(public_data)
        else:
            self.data = i2p_b64decode(public_data)
            self.base64 = str(public_data).strip()

    @staticmethod
    def _read_cert_len(private_data: bytes) -> int:
        if len(private_data) < 387:
            raise ValueError("Private destination blob is too short")
        return struct.unpack("!H", private_data[385:387])[0]

    @property
    def base32(self) -> str:
        return b32encode(sha256(self.data).digest()).decode("ascii")[:52].lower()

    def __repr__(self) -> str:
        return f"<Destination: {self.base32}>"

    def __str__(self) -> str:
        return self.base64


__all__ = ["Destination", "PrivateKey", "i2p_b64decode", "i2p_b64encode"]
