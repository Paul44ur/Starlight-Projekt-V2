"""Reading a certificate that does not verify: an expired, self-signed one on a real local
TLS server. Its names and dates must still come back (certs reports it as expired; recon
shows its details) instead of an empty dict or a bare "did not verify"."""

from __future__ import annotations

import datetime as dt
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from starlight.core.tlsinfo import decode_der, read_certificate

crypto = pytest.importorskip("cryptography")


def expired_cert(folder: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "old.example")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=400))
        .not_valid_after(now - dt.timedelta(days=10))  # expired ten days ago
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_file, key_file


@pytest.fixture
def tls_server(tmp_path: Path) -> Iterator[int]:
    cert_file, key_file = expired_cert(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                with context.wrap_socket(conn, server_side=True):
                    pass
            except (ssl.SSLError, OSError):
                conn.close()  # the verifying client hangs up on a bad certificate

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield port
    stop.set()
    thread.join(2)
    listener.close()


def test_an_expired_self_signed_certificate_is_still_read(tls_server: int) -> None:
    cert = read_certificate("localhost", tls_server, timeout=5)
    assert "verify_error" in cert  # it did not verify ...
    subject = {k: v for rdn in cert["subject"] for k, v in rdn}
    assert subject["commonName"] == "old.example"  # ... but we still know what it is
    assert ssl.cert_time_to_seconds(cert["notAfter"]) < dt.datetime.now(dt.UTC).timestamp()


def test_decode_der_handles_nothing() -> None:
    assert decode_der(None) == {} and decode_der(b"") == {}
