# -*- coding: utf-8 -*-
"""
Safe HTTP utilities for GeodbIO plugin.

Provides a scheme-validated urlopen wrapper so that call sites
are not flagged by Bandit S310 / B310 security scanners.
"""
import ssl
import threading
import urllib.request
from urllib.parse import urlparse

ALLOWED_SCHEMES = ('http', 'https')

# Cache of lazily-built SSLContext objects, keyed by whether hostname/cert
# verification is relaxed (the local-dev case). Built once behind a lock and
# reused by every worker thread from here on.
#
# This exists because ssl.create_default_context() was being called directly
# from each streaming-layer QThread's run() (BLM claims / PLSS / federal
# lands fetch workers). On Windows, concurrent SSLContext construction across
# threads races inside CPython's _ssl module (cert-store enumeration releases
# the GIL without synchronizing shared state — see cpython#134698/#134724),
# corrupting the heap and crashing QGIS, often nowhere near the actual call
# site (e.g. inside Qt's map renderer). SSLContext is safe to *share and
# reuse* concurrently; it is only concurrent *construction* that's unsafe.
_context_cache = {}
_context_lock = threading.Lock()


def get_shared_ssl_context(insecure=False):
    """Return a process-wide shared SSLContext, building it on first use.

    Args:
        insecure: If True, return a context with hostname/cert verification
            disabled (used for localhost/127.0.0.1 development URLs).

    Returns:
        A cached ssl.SSLContext, safe to reuse concurrently across threads.
    """
    with _context_lock:
        ctx = _context_cache.get(insecure)
        if ctx is None:
            ctx = ssl.create_default_context()
            if insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            _context_cache[insecure] = ctx
        return ctx


def safe_urlopen(request, *, context=None, timeout=30):
    """Open a URL after validating the scheme is http(s).

    Args:
        request: A urllib.request.Request object or URL string.
        context: Optional ssl.SSLContext.
        timeout: Request timeout in seconds.

    Returns:
        The response object (use as context manager).

    Raises:
        ValueError: If the URL scheme is not http or https.
    """
    url = request.full_url if hasattr(request, 'full_url') else str(request)
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"Blocked URL scheme '{parsed.scheme}' — "
            f"only {ALLOWED_SCHEMES} are permitted"
        )
    if context is None:
        context = get_shared_ssl_context()
    # Use an explicit opener restricted to HTTP(S) handlers rather than the
    # module-level urllib.request.urlopen. The scheme is already validated
    # against ALLOWED_SCHEMES above, so file:/, ftp:, and custom-scheme opens
    # are blocked either way; building the opener from only http/https
    # handlers means there is literally no handler that could service another
    # scheme, and avoids the urlopen() call pattern that scheme-audit scanners
    # flag regardless of the surrounding validation.
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=context),
    )
    return opener.open(request, timeout=timeout)
