# -*- coding: utf-8 -*-
"""
Safe HTTP utilities for GeodbIO plugin.

Provides a scheme-validated urlopen wrapper so that call sites
are not flagged by Bandit S310 / B310 security scanners.
"""
import ssl
import urllib.request
from urllib.parse import urlparse

ALLOWED_SCHEMES = ('http', 'https')


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
        context = ssl.create_default_context()
    # Scheme is validated against ALLOWED_SCHEMES above, so file:/ and
    # custom-scheme opens are already blocked. Suppress for both Ruff
    # (S310) and standalone Bandit (B310).
    return urllib.request.urlopen(  # noqa: S310  # nosec B310
        request, context=context, timeout=timeout
    )
