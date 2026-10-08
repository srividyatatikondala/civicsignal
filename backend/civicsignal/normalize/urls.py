"""
URL canonicalization and domain identity.

canonical_url() decides when two links are "the same page", which is what
stops one page from being counted as several independent sources. Rules
(documented in docs/methodology.md):
  * scheme forced to https; host lowercased; leading www./m./amp. removed
  * default ports, fragments (incl. Google's #:~:text=) and tracking params removed
  * remaining query params sorted; trailing slash and trailing /amp removed
  * YouTube: youtu.be/ID, /shorts/ID, /watch?v=ID -> youtube.com/watch?v=ID

registrable_domain() is India-aware (gov.in, nic.in, co.in, ...) without a
network-fetched public-suffix list; unknown multi-level suffixes fall back
to the last two labels. This is a known limitation.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "gclid", "fbclid", "dclid", "msclkid", "srsltid", "ved", "ei", "sa", "usg",
    "igshid", "mc_cid", "mc_eid", "_ga", "ref", "ref_src", "feature", "si",
}
_TRACKING_PREFIXES = ("utm_",)
_STRIP_HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")

# Multi-label public suffixes that matter for this project's (Indian) results.
_MULTI_LABEL_SUFFIXES = {
    "gov.in", "nic.in", "co.in", "org.in", "net.in", "ac.in", "edu.in", "res.in",
    "ernet.in", "mil.in", "firm.in", "gen.in", "ind.in",
    "co.uk", "ac.uk", "gov.uk", "org.uk", "com.au", "gov.au", "co.jp",
}

_YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def _clean_host(host: str) -> str:
    host = host.lower().rstrip(".")
    changed = True
    while changed:
        changed = False
        for prefix in _STRIP_HOST_PREFIXES:
            if host.startswith(prefix) and host.count(".") >= 2:
                host = host[len(prefix):]
                changed = True
    return host


def host_of(url: str | None) -> str | None:
    """Host without www./m. prefixes, e.g. 'uidai.gov.in'. None if unparseable."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return _clean_host(parts.hostname)


def registrable_domain(host: str | None) -> str | None:
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def _youtube_canonical(host: str, path: str, query: dict[str, str]) -> str | None:
    video_id = None
    if host == "youtu.be":
        video_id = path.strip("/").split("/")[0]
    elif path.startswith("/shorts/") or path.startswith("/embed/") or path.startswith("/live/"):
        video_id = path.split("/")[2] if len(path.split("/")) > 2 else None
    elif path == "/watch":
        video_id = query.get("v")
    if video_id and _YT_ID.match(video_id):
        return f"https://youtube.com/watch?v={video_id}"
    return None


def canonical_url(url: str | None) -> str | None:
    """Canonical form for page identity, or None for missing/unparseable/non-http URLs."""
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None

    host = _clean_host(parts.hostname)
    if port and port not in (80, 443):
        host = f"{host}:{port}"

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith(_TRACKING_PREFIXES)
    ]

    if registrable_domain(host.split(":")[0]) in _YOUTUBE_HOSTS:
        yt = _youtube_canonical(host.split(":")[0], parts.path, dict(query_pairs))
        if yt:
            return yt

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path.endswith("/amp") or path.endswith("/amp/"):
        path = path.rsplit("/amp", 1)[0] or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    query = urlencode(sorted(query_pairs))
    return urlunsplit(("https", host, path, query, ""))
