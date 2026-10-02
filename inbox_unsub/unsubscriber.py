"""Perform the actual unsubscribe requests."""

import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import parse_qs, unquote, urlsplit

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 inbox-unsub")

DONE, VISITED, MANUAL, FAILED, DRY_RUN = "done", "visited", "manual", "failed", "dry-run"


@dataclass
class Result:
    status: str
    method: str
    detail: str = ""
    url: str = ""


def _request(url, data=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(65536)
        return resp.status


def one_click(url, timeout=20):
    """RFC 8058: POST 'List-Unsubscribe=One-Click' to the HTTPS URL."""
    return _request(url, data=b"List-Unsubscribe=One-Click", timeout=timeout)


def visit(url, timeout=20):
    return _request(url, timeout=timeout)


def parse_mailto(uri):
    parts = urlsplit(uri)
    to = unquote(parts.path)
    query = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
    to = to or query.get("to", "")
    return to, query.get("subject", "unsubscribe"), query.get("body", "unsubscribe")


@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str = None
    oauth_token: str = None


def send_mailto(uri, smtp: SmtpConfig, from_addr):
    to, subject, body = parse_mailto(uri)
    if not to:
        raise ValueError("mailto link has no address")
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    context = ssl.create_default_context()
    if smtp.port == 465:
        server = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=30, context=context)
    else:
        server = smtplib.SMTP(smtp.host, smtp.port, timeout=30)
        server.starttls(context=context)
    with server:
        if smtp.oauth_token:
            import base64
            auth = f"user={smtp.user}\x01auth=Bearer {smtp.oauth_token}\x01\x01"
            code, resp = server.docmd("AUTH", "XOAUTH2 " + base64.b64encode(auth.encode()).decode())
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, resp)
        else:
            server.login(smtp.user, smtp.password)
        server.send_message(msg)
    return to


def unsubscribe(sender, smtp=None, from_addr=None, dry_run=False, visit_links=True):
    """Try each available method, strongest first. Returns a Result."""
    errors = []
    if sender.one_click and sender.http_urls:
        url = sender.http_urls[0]
        if dry_run:
            return Result(DRY_RUN, "one-click", "would POST one-click request", url)
        try:
            status = one_click(url)
            if 200 <= status < 300:
                return Result(DONE, "one-click", f"HTTP {status}", url)
            errors.append(f"one-click HTTP {status}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"one-click: {exc}")

    if sender.mailto_urls and smtp is not None:
        uri = sender.mailto_urls[0]
        if dry_run:
            return Result(DRY_RUN, "email", f"would email {parse_mailto(uri)[0]}", uri)
        try:
            to = send_mailto(uri, smtp, from_addr)
            return Result(DONE, "email", f"sent unsubscribe email to {to}", uri)
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            errors.append(f"email: {exc}")

    if sender.http_urls and visit_links:
        url = sender.http_urls[0]
        if dry_run:
            return Result(DRY_RUN, "link", "would open unsubscribe link", url)
        try:
            status = visit(url)
            if 200 <= status < 300:
                return Result(VISITED, "link",
                              "opened link - some senders need you to click a confirm button", url)
            errors.append(f"link HTTP {status}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors.append(f"link: {exc}")

    manual = (sender.http_urls or sender.body_links or [""])[0]
    if manual:
        return Result(MANUAL, "manual", "; ".join(errors) or "open the link in your browser", manual)
    return Result(FAILED, "none", "; ".join(errors) or "no unsubscribe method found")
