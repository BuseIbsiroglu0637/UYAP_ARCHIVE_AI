"""Ephemeral TLS certificate for localhost -> Android emulator (synthetic only).

Requires cryptography package. No CA trust-store modification. Cert includes
IP SAN 10.0.2.2 for Android emulator host loopback alias; client pins SHA-256
of the exact DER cert out of band. Key files exist only briefly for SSL loading.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import ipaddress
import ssl
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@contextmanager
def ephemeral_server_context():
    now = datetime.now(timezone.utc)
    private = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Hukuk Arsivim synthetic emulator")
    ])
    cert = (x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(hours=6))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.IPAddress(ipaddress.ip_address("10.0.2.2")),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.DNSName("localhost")
            ]), critical=False)
        .sign(private, hashes.SHA256()))
    fingerprint = hashlib.sha256(cert.public_bytes(
        serialization.Encoding.DER)).hexdigest().upper()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    # Python's stdlib SSLContext.load_cert_chain requires files.
    # Windows file handles must be closed before the context loads.
    with tempfile.TemporaryDirectory(prefix="hukukarsivim-tls-") as folder:
        base = Path(folder)
        keypath, certpath = base / "key.pem", base / "cert.pem"
        keypath.write_bytes(private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()
        ))
        certpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        try:
            context.load_cert_chain(str(certpath), str(keypath))
        finally:
            keypath.unlink(missing_ok=True)
            certpath.unlink(missing_ok=True)
    yield context, fingerprint
