"""Reading a server's TLS certificate, including one that does not verify.

A verifying TLS connection refuses an expired or self-signed certificate, and an
unverified one makes ``getpeercert()`` return nothing. To still show who a certificate is
for and when it expires, the raw certificate is taken from an unverified connection and
parsed with Python's own certificate parser (standard library only).
"""

from __future__ import annotations

import contextlib
import os
import socket
import ssl
import tempfile
from typing import Any


def unverified_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # to SEE the certificate, not to trust it
    return context


def decode_der(der: bytes | None) -> dict[str, Any]:
    """A DER certificate as the dict ``getpeercert()`` returns for a verified one."""
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if not der or decoder is None:
        return {}
    handle, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(handle, "w", encoding="ascii") as file:
            file.write(ssl.DER_cert_to_PEM_cert(der))
        return dict(decoder(path))
    except Exception:
        return {}
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


def read_certificate(host: str, port: int, timeout: float) -> dict[str, Any]:
    """The certificate ``host`` presents, with ``verify_error`` set when it does not verify
    (expired, self-signed, wrong name ...). Raises OSError/ssl.SSLError when no TLS
    connection can be made at all."""
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as sock,
            ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls,
        ):
            return tls.getpeercert() or {}
    except ssl.SSLCertVerificationError as exc:
        problem = exc.verify_message or str(exc)
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        unverified_context().wrap_socket(sock, server_hostname=host) as tls,
    ):
        cert = decode_der(tls.getpeercert(binary_form=True))
    cert["verify_error"] = problem
    return cert
