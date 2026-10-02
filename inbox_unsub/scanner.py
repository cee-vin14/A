"""Read message headers over IMAP and group them by sender."""

import email
import imaplib
import re
from dataclasses import dataclass, field
from datetime import timedelta

from . import headers as h

FETCH_FIELDS = ("FROM DATE SUBJECT LIST-UNSUBSCRIBE LIST-UNSUBSCRIBE-POST "
                "LIST-ID AUTHENTICATION-RESULTS PRECEDENCE")
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Sender:
    address: str
    name: str = ""
    count: int = 0
    dates: list = field(default_factory=list)
    subjects: list = field(default_factory=list)
    http_urls: list = field(default_factory=list)
    mailto_urls: list = field(default_factory=list)
    body_links: list = field(default_factory=list)
    one_click: bool = False
    list_id: str = ""
    dkim: object = None          # True / False / None (unknown)
    messages: list = field(default_factory=list)   # (folder, uid) pairs
    _latest_unsub_date: object = None

    @property
    def domain(self):
        return self.address.rsplit("@", 1)[-1]

    @property
    def has_header_unsubscribe(self):
        return bool(self.http_urls or self.mailto_urls)

    @property
    def can_unsubscribe(self):
        return self.has_header_unsubscribe or bool(self.body_links)

    @property
    def method(self):
        if self.one_click and self.http_urls:
            return "one-click"
        if self.mailto_urls:
            return "email"
        if self.http_urls:
            return "link"
        if self.body_links:
            return "manual"
        return "none"

    @property
    def avg_gap_days(self):
        dated = sorted(d for d in self.dates if d)
        if len(dated) < 2:
            return None
        return (dated[-1] - dated[0]).total_seconds() / 86400 / (len(dated) - 1)

    @property
    def frequency(self):
        gap = self.avg_gap_days
        if gap is None:
            return "one-off"
        if gap <= 1.5:
            return "daily"
        if gap <= 4:
            return "several/week"
        if gap <= 8:
            return "weekly"
        if gap <= 35:
            return "monthly"
        return "occasional"

    @property
    def last_seen(self):
        dated = [d for d in self.dates if d]
        return max(dated) if dated else None

    def add(self, folder, uid, msg):
        name, _ = h.parse_sender(msg.get("From", ""))
        date = h.parse_date(msg.get("Date"))
        self.count += 1
        self.dates.append(date)
        self.messages.append((folder, uid))
        if name and not self.name:
            self.name = name
        subject = h.decode(msg.get("Subject", "")).strip()
        if subject and len(self.subjects) < 3 and subject not in self.subjects:
            self.subjects.append(subject)
        if msg.get("List-ID") and not self.list_id:
            self.list_id = h.decode(msg.get("List-ID")).strip()
        dkim = h.dkim_status(msg.get("Authentication-Results", ""))
        if dkim is not None:
            self.dkim = dkim if self.dkim is None else (self.dkim or dkim)

        http_urls, mailto_urls = h.parse_list_unsubscribe(msg.get("List-Unsubscribe", ""))
        if http_urls or mailto_urls:
            # Unsubscribe links are often per-message tokens; keep the newest.
            newest = self._latest_unsub_date
            if newest is None or (date is not None and date >= newest):
                self._latest_unsub_date = date or newest
                self.http_urls, self.mailto_urls = http_urls, mailto_urls
                self.one_click = h.is_one_click(msg.get("List-Unsubscribe-Post", ""))


def imap_date(dt):
    return f"{dt.day:02d}-{_MONTHS[dt.month - 1]}-{dt.year}"


def quote_mailbox(name):
    if name.startswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect(host, port, user, password=None, oauth_token=None):
    conn = imaplib.IMAP4_SSL(host, port)
    if oauth_token:
        auth = f"user={user}\x01auth=Bearer {oauth_token}\x01\x01"
        conn.authenticate("XOAUTH2", lambda _: auth.encode())
    else:
        conn.login(user, password)
    return conn


def scan_folder(conn, folder, days, senders, progress=None, batch=250):
    """Fetch headers for messages newer than `days` and add them to `senders`."""
    typ, _ = conn.select(quote_mailbox(folder), readonly=True)
    if typ != "OK":
        raise RuntimeError(f"Could not open folder {folder!r}")
    since = imap_date(h.utcnow() - timedelta(days=days))
    typ, data = conn.uid("SEARCH", None, "SINCE", since)
    if typ != "OK":
        raise RuntimeError(f"Search failed in {folder!r}")
    uids = data[0].split() if data and data[0] else []
    done = 0
    for start in range(0, len(uids), batch):
        chunk = uids[start:start + batch]
        typ, data = conn.uid("FETCH", b",".join(chunk).decode(),
                             f"(BODY.PEEK[HEADER.FIELDS ({FETCH_FIELDS})])")
        if typ != "OK":
            continue
        for item in data:
            if not isinstance(item, tuple):
                continue
            match = re.search(rb"UID (\d+)", item[0])
            if not match:
                continue
            msg = email.message_from_bytes(item[1])
            _, address = h.parse_sender(msg.get("From", ""))
            if not address:
                continue
            sender = senders.setdefault(address, Sender(address))
            sender.add(folder, match.group(1).decode(), msg)
        done += len(chunk)
        if progress:
            progress(folder, done, len(uids))
    return len(uids)


def fetch_body_links(conn, sender):
    """Look inside the newest message from `sender` for unsubscribe links."""
    folder, uid = sender.messages[-1]
    conn.select(quote_mailbox(folder), readonly=True)
    typ, data = conn.uid("FETCH", uid, "(BODY.PEEK[])")
    if typ != "OK" or not data or not isinstance(data[0], tuple):
        return []
    msg = email.message_from_bytes(data[0][1])
    links = []
    for part in msg.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        links.extend(l for l in h.find_unsubscribe_links(text) if l not in links)
    return links


def move_messages(conn, messages, destination):
    """Move (folder, uid) pairs to `destination`. Requires the IMAP MOVE extension."""
    _, caps = conn.capability()
    if b"MOVE" not in caps[0].upper().split():
        raise RuntimeError("This mail server does not support IMAP MOVE; skipping tidy-up.")
    conn.create(quote_mailbox(destination))   # harmless error if it already exists
    by_folder = {}
    for folder, uid in messages:
        by_folder.setdefault(folder, []).append(uid)
    moved = 0
    for folder, uids in by_folder.items():
        typ, _ = conn.select(quote_mailbox(folder), readonly=False)
        if typ != "OK":
            continue
        for start in range(0, len(uids), 250):
            chunk = ",".join(uids[start:start + 250])
            typ, _ = conn.uid("MOVE", chunk, quote_mailbox(destination))
            if typ == "OK":
                moved += len(uids[start:start + 250])
    return moved
