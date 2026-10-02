"""Parsing helpers for email headers and message bodies."""

import html
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime


def decode(value) -> str:
    """Decode RFC 2047 encoded header text (e.g. =?UTF-8?B?...?=)."""
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def parse_sender(from_header: str):
    """Return (display_name, lowercase_address) from a From: header."""
    name, addr = parseaddr(decode(from_header))
    return name.strip().strip('"'), addr.strip().lower()


def parse_date(date_header: str):
    """Parse a Date: header into an aware UTC datetime, or None."""
    if not date_header:
        return None
    try:
        dt = parsedate_to_datetime(str(date_header))
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_list_unsubscribe(value: str):
    """Split a List-Unsubscribe header into (http_urls, mailto_urls).

    The header is a comma separated list of <...> wrapped URIs (RFC 2369).
    Long headers are often folded over several lines, so whitespace inside
    the angle brackets is removed.
    """
    http_urls, mailto_urls = [], []
    if not value:
        return http_urls, mailto_urls
    for raw in re.findall(r"<([^>]+)>", decode(value)):
        uri = re.sub(r"\s+", "", raw)
        lower = uri.lower()
        if lower.startswith(("http://", "https://")):
            http_urls.append(uri)
        elif lower.startswith("mailto:"):
            mailto_urls.append(uri)
    return http_urls, mailto_urls


def is_one_click(list_unsubscribe_post: str) -> bool:
    """True if the sender supports RFC 8058 one-click unsubscribe."""
    return "list-unsubscribe=one-click" in decode(list_unsubscribe_post).lower().replace(" ", "")


def dkim_status(auth_results: str):
    """Return True (dkim=pass), False (dkim present but not pass) or None (unknown)."""
    text = decode(auth_results).lower()
    if not text:
        return None
    results = re.findall(r"dkim=(\w+)", text)
    if not results:
        return None
    return "pass" in results


_ANCHOR = re.compile(r"<a\b[^>]*?href\s*=\s*([\"'])(.*?)\1[^>]*>(.*?)</a>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def find_unsubscribe_links(html_body: str):
    """Find links in an HTML body whose text or URL mentions unsubscribing."""
    links = []
    for _, href, text in _ANCHOR.findall(html_body or ""):
        href = html.unescape(href.strip())
        label = _TAGS.sub("", text).lower()
        if not href.lower().startswith(("http://", "https://")):
            continue
        if re.search(r"unsub|opt[\s-]?out|email preferences|manage (your )?(email|subscription|preferences)",
                     label + " " + href.lower()):
            if href not in links:
                links.append(href)
    return links


def utcnow():
    return datetime.now(timezone.utc)
