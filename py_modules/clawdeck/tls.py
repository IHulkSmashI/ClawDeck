"""One SSL context for every HTTPS request the backend makes.

Decky Loader ships its own Python, whose ssl module finds no CA store on Bazzite (the first on-device run failed
every download with CERTIFICATE_VERIFY_FAILED "unable to get local issuer certificate"). The system's bundle is
where Fedora/Bazzite keep it; the first path that exists is used, and the default context is the fallback so a
distro that does ship certificates to Python still works. Verification is never disabled.
"""
from __future__ import annotations

import os
import ssl
import urllib.request

CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",      # Bazzite / Fedora (symlink into /etc/pki)
    "/etc/pki/tls/certs/ca-bundle.crt",        # Fedora, RHEL
    "/etc/ssl/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",                       # Arch / SteamOS
)

_ctx: ssl.SSLContext | None = None


def context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        for path in CA_BUNDLES:
            if os.path.exists(path):
                _ctx = ssl.create_default_context(cafile=path)
                break
        else:
            _ctx = ssl.create_default_context()
    return _ctx


def urlopen(req, timeout: float):
    """urllib.request.urlopen with the system CA bundle."""
    return urllib.request.urlopen(req, timeout=timeout, context=context())
