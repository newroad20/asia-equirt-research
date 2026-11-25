"""Network helpers.

Some networks (company PCs, security software) re-sign HTTPS traffic with their own root
certificate. Python's default bundle (certifi) does not trust it, so Yahoo and the Claude API
fail with "self signed certificate in certificate chain". On Windows we build one PEM bundle
from certifi + the Windows certificate store and use it for every request.
"""
import os
import ssl
import sys
import tempfile
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def ca_bundle() -> str:
    """Path to a PEM file with certifi roots plus (on Windows) the OS-trusted roots."""
    if sys.platform != "win32":
        return certifi.where()
    server_auth = "1.3.6.1.5.5.7.3.1"
    pem = [open(certifi.where(), encoding="utf-8").read()]
    # Only the trusted-root store, and only certificates Windows trusts for server authentication
    # (trust is True = all purposes, or a set of allowed purpose OIDs).
    for cert, encoding, trust in ssl.enum_certificates("ROOT"):
        if encoding == "x509_asn" and (trust is True or server_auth in trust):
            pem.append(ssl.DER_cert_to_PEM_cert(cert))
    path = os.path.join(tempfile.gettempdir(), "asia_research_ca_bundle.pem")
    # Write to a unique temp file, then swap it in, so a second process never reads a half-written file.
    fd, tmp = tempfile.mkstemp(suffix=".pem", dir=tempfile.gettempdir())
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(pem))
    os.replace(tmp, path)
    return path


def yf_session():
    """curl_cffi session for yfinance that trusts the bundle above."""
    from curl_cffi import requests as curl_requests

    return curl_requests.Session(impersonate="chrome", verify=ca_bundle())


def anthropic_client():
    """Anthropic client that trusts the bundle above. Reads credentials from the environment."""
    import anthropic

    ctx = ssl.create_default_context(cafile=ca_bundle())
    return anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(verify=ctx))
