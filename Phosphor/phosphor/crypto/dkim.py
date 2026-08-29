"""DKIM key generation and message signing.

Phosphor generates a 2048-bit RSA keypair per domain. The private key (PKCS#8
PEM) is stored in the DB; the public key is published as a TXT record at
`<selector>._domainkey.<domain>`.
"""
from __future__ import annotations

import base64
import re
import textwrap

import dkim as dkimlib
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_keypair() -> tuple[str, str]:
    """Return (private_pem, public_key_b64_der)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_b64 = base64.b64encode(public_der).decode("ascii")
    return private_pem, public_b64


def dkim_txt_value(public_b64: str) -> str:
    """The full value for the `<selector>._domainkey` TXT record."""
    return f"v=DKIM1; h=sha256; k=rsa; p={public_b64}"


def dkim_txt_split(public_b64: str, chunk: int = 250) -> list[str]:
    """Some DNS UIs need the TXT value pre-split into <=255-char strings."""
    value = dkim_txt_value(public_b64)
    return textwrap.wrap(value, chunk)


def sign_message(
    raw_message: bytes,
    *,
    domain: str,
    selector: str,
    private_pem: str,
    headers: list[bytes] | None = None,
) -> bytes:
    """Return `raw_message` with a DKIM-Signature header prepended."""
    if headers is None:
        headers = [b"From", b"To", b"Subject", b"Date", b"Message-ID",
                   b"MIME-Version", b"Content-Type", b"Reply-To", b"List-Unsubscribe"]
    sig = dkimlib.sign(
        message=raw_message,
        selector=selector.encode("ascii"),
        domain=domain.encode("ascii"),
        privkey=private_pem.encode("ascii"),
        include_headers=headers,
        canonicalize=(b"relaxed", b"relaxed"),
    )
    return sig + raw_message


_TXT_RE = re.compile(r"p=([A-Za-z0-9+/=]+)")


def public_b64_from_txt(txt: str) -> str | None:
    m = _TXT_RE.search(txt.replace('" "', "").replace(" ", ""))
    return m.group(1) if m else None
