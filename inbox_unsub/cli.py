"""Command line interface."""

import argparse
import getpass
import html
import json
import os
import sys
import time
from pathlib import Path

from . import scanner, unsubscriber
from .headers import utcnow
from .providers import PROVIDERS, guess_provider

STATE_DIR = Path(os.environ.get("INBOX_UNSUB_HOME", Path.home() / ".inbox_unsub"))
FREQ_ORDER = ["daily", "several/week", "weekly", "monthly", "occasional", "one-off"]


def build_parser():
    p = argparse.ArgumentParser(
        prog="inbox-unsub",
        description="Scan your inbox for marketing emails and unsubscribe from them. "
                    "Runs entirely on your own computer.")
    p.add_argument("email", help="your email address")
    p.add_argument("--provider", choices=sorted(PROVIDERS),
                   help="mail provider (guessed from your address if omitted)")
    p.add_argument("--imap-host"), p.add_argument("--imap-port", type=int)
    p.add_argument("--smtp-host"), p.add_argument("--smtp-port", type=int)
    p.add_argument("--oauth-token", help="OAuth2 access token (needed for most Outlook/Hotmail accounts)")
    p.add_argument("--folder", action="append",
                   help="folder to scan (repeatable, default INBOX)")
    p.add_argument("--days", type=int, default=90, help="how far back to look (default 90)")
    p.add_argument("--min-count", type=int, default=2,
                   help="only list senders who emailed at least this many times (default 2)")
    p.add_argument("--frequency", choices=FREQ_ORDER,
                   help="only list senders at least this frequent, e.g. 'weekly'")
    p.add_argument("--keep", action="append", default=[],
                   help="never unsubscribe from addresses/domains containing this text (repeatable)")
    p.add_argument("--deep", action="store_true",
                   help="also look inside emails that lack an unsubscribe header (slower)")
    p.add_argument("--all", action="store_true", help="select every listed sender without asking")
    p.add_argument("--yes", "-y", action="store_true", help="don't ask for confirmation")
    p.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    p.add_argument("--no-visit", action="store_true",
                   help="don't open plain unsubscribe links automatically (just list them)")
    p.add_argument("--include-unverified", action="store_true",
                   help="include senders whose DKIM signature failed (possible spoofing)")
    p.add_argument("--move-to", metavar="FOLDER",
                   help="after unsubscribing, move the old emails from those senders to FOLDER")
    p.add_argument("--report", default="unsubscribe-report.html", help="where to write the HTML report")
    p.add_argument("--include-done", action="store_true",
                   help="show senders you already unsubscribed from in a previous run")
    return p


# ---------------------------------------------------------------- state

def load_state():
    path = STATE_DIR / "state.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"unsubscribed": {}, "keep": []}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True))


# ---------------------------------------------------------------- selection

def parse_selection(text, n):
    """Parse '1,3,5-7' / 'all' into a sorted list of 0-based indexes."""
    text = text.strip().lower()
    if text in ("all", "a", "*"):
        return list(range(n))
    chosen = set()
    for part in text.replace(" ", ",").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = int(lo), int(hi)
            if lo > hi:
                lo, hi = hi, lo
            chosen.update(range(lo, hi + 1))
        else:
            chosen.add(int(part))
    bad = [i for i in chosen if not 1 <= i <= n]
    if bad:
        raise ValueError(f"numbers out of range: {sorted(bad)}")
    return sorted(i - 1 for i in chosen)


def filter_senders(senders, args, state):
    keep = [k.lower() for k in args.keep + state.get("keep", [])]
    max_freq = FREQ_ORDER.index(args.frequency) if args.frequency else len(FREQ_ORDER)
    out = []
    for s in senders.values():
        if not s.can_unsubscribe or s.count < args.min_count:
            continue
        if any(k in s.address for k in keep):
            continue
        if FREQ_ORDER.index(s.frequency) > max_freq:
            continue
        if s.dkim is False and not args.include_unverified:
            continue
        if s.address in state["unsubscribed"] and not args.include_done:
            continue
        out.append(s)
    out.sort(key=lambda s: (-s.count, s.address))
    return out


def print_table(rows):
    print()
    print(f"{'#':>3}  {'Emails':>6}  {'How often':<12}  {'Method':<9}  Sender")
    print(f"{'-'*3}  {'-'*6}  {'-'*12}  {'-'*9}  {'-'*40}")
    for i, s in enumerate(rows, 1):
        who = f"{s.name} <{s.address}>" if s.name else s.address
        if len(who) > 70:
            who = who[:67] + "..."
        print(f"{i:>3}  {s.count:>6}  {s.frequency:<12}  {s.method:<9}  {who}")
    print()


# ---------------------------------------------------------------- report

def write_report(path, results, skipped):
    rows = []
    for s, r in results:
        link = (f'<a href="{html.escape(r.url)}" target="_blank" rel="noopener">open</a>'
                if r.url.startswith("http") else html.escape(r.url))
        rows.append(f"<tr class='{r.status}'><td>{html.escape(s.name or '')}<br><small>"
                    f"{html.escape(s.address)}</small></td><td>{s.count}</td><td>{s.frequency}</td>"
                    f"<td>{r.status}</td><td>{html.escape(r.detail)}</td><td>{link}</td></tr>")
    for s in skipped:
        url = (s.http_urls or s.body_links or [""])[0]
        link = f'<a href="{html.escape(url)}" target="_blank" rel="noopener">open</a>' if url else ""
        rows.append(f"<tr class='skipped'><td>{html.escape(s.name or '')}<br><small>"
                    f"{html.escape(s.address)}</small></td><td>{s.count}</td><td>{s.frequency}</td>"
                    f"<td>not selected</td><td></td><td>{link}</td></tr>")
    Path(path).write_text(f"""<!doctype html><meta charset="utf-8">
<title>Unsubscribe report</title>
<style>
body{{font:14px system-ui,sans-serif;margin:24px;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left}}
tr.done td:nth-child(4){{color:#1a7f37;font-weight:600}}tr.visited td:nth-child(4){{color:#9a6700}}
tr.manual td:nth-child(4),tr.failed td:nth-child(4){{color:#cf222e;font-weight:600}}
tr.skipped{{color:#888}}small{{color:#666}}
</style>
<h1>Unsubscribe report</h1>
<p>Generated {utcnow():%Y-%m-%d %H:%M} UTC. <b>done</b> = confirmed;
<b>visited</b> = link opened, check whether it asks you to confirm;
<b>manual</b>/<b>failed</b> = click the link yourself.</p>
<table><tr><th>Sender</th><th>Emails</th><th>How often</th><th>Status</th><th>Detail</th><th>Link</th></tr>
{''.join(rows)}</table>""", encoding="utf-8")


# ---------------------------------------------------------------- main

def resolve_settings(args):
    key = args.provider or guess_provider(args.email)
    provider = PROVIDERS.get(key)
    imap_host = args.imap_host or (provider and provider.imap_host)
    if not imap_host:
        sys.exit("Couldn't guess your mail provider. Pass --provider or --imap-host/--smtp-host.")
    imap_port = args.imap_port or (provider.imap_port if provider else 993)
    smtp_host = args.smtp_host or (provider and provider.smtp_host)
    smtp_port = args.smtp_port or (provider.smtp_port if provider else 465)
    return provider, imap_host, imap_port, smtp_host, smtp_port


def ask(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def main(argv=None):
    args = build_parser().parse_args(argv)
    provider, imap_host, imap_port, smtp_host, smtp_port = resolve_settings(args)
    if provider:
        print(f"Provider: {provider.name}")
        if provider.notes:
            print(f"  Note: {provider.notes}")

    password = None
    if not args.oauth_token:
        password = os.environ.get("INBOX_UNSUB_PASSWORD") or getpass.getpass(
            f"Password / app password for {args.email}: ")

    print(f"Connecting to {imap_host}...")
    try:
        conn = scanner.connect(imap_host, imap_port, args.email, password, args.oauth_token)
    except Exception as exc:  # imaplib raises a variety of errors
        sys.exit(f"Login failed: {exc}\nTip: most providers need an *app password*, not your normal one.")

    state = load_state()
    senders = {}

    def progress(folder, done, total):
        print(f"\r  {folder}: {done}/{total} emails", end="", flush=True)

    try:
        for folder in args.folder or ["INBOX"]:
            print(f"Scanning {folder} (last {args.days} days)...")
            total = scanner.scan_folder(conn, folder, args.days, senders, progress)
            print(f"\r  {folder}: {total} emails scanned" + " " * 10)

        if args.deep:
            candidates = [s for s in senders.values()
                          if not s.has_header_unsubscribe and s.count >= max(args.min_count, 3)]
            print(f"Looking inside emails from {len(candidates)} senders without unsubscribe headers...")
            for s in candidates:
                try:
                    s.body_links = scanner.fetch_body_links(conn, s)
                except Exception as exc:
                    print(f"  skipped {s.address}: {exc}")

        rows = filter_senders(senders, args, state)
        if not rows:
            print("No marketing senders found that match your filters. Nice and tidy!")
            return 0

        print(f"Found {len(rows)} mailing-list senders "
              f"({sum(s.count for s in rows)} emails in the last {args.days} days):")
        print_table(rows)

        if args.all:
            picked = list(range(len(rows)))
        else:
            while True:
                answer = ask("Which do you want to unsubscribe from? "
                             "(e.g. 1,3,5-9 / all / Enter to quit): ")
                if not answer.strip():
                    write_report(args.report, [], rows)
                    print(f"Nothing changed. Links for every sender are in {args.report}")
                    return 0
                try:
                    picked = parse_selection(answer, len(rows))
                    break
                except ValueError as exc:
                    print(f"  {exc}")
        chosen = [rows[i] for i in picked]

        verb = "Pretend to unsubscribe" if args.dry_run else "Unsubscribe"
        if not args.yes and ask(f"{verb} from {len(chosen)} senders? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

        smtp = None
        if smtp_host:
            smtp = unsubscriber.SmtpConfig(smtp_host, smtp_port, args.email, password, args.oauth_token)

        results = []
        for i, s in enumerate(chosen, 1):
            r = unsubscriber.unsubscribe(s, smtp=smtp, from_addr=args.email,
                                         dry_run=args.dry_run, visit_links=not args.no_visit)
            results.append((s, r))
            print(f"[{i}/{len(chosen)}] {r.status:<8} {s.address}  ({r.method}: {r.detail})")
            if r.status in (unsubscriber.DONE, unsubscriber.VISITED):
                state["unsubscribed"][s.address] = {
                    "date": utcnow().isoformat(timespec="seconds"), "method": r.method}
            time.sleep(0.3)

        if not args.dry_run:
            save_state(state)

        if args.move_to and not args.dry_run:
            done = [s for s, r in results if r.status in (unsubscriber.DONE, unsubscriber.VISITED)]
            messages = [m for s in done for m in s.messages]
            if messages:
                try:
                    moved = scanner.move_messages(conn, messages, args.move_to)
                    print(f"Moved {moved} old emails to '{args.move_to}'.")
                except Exception as exc:
                    print(f"Couldn't move old emails: {exc}")

        chosen_ids = {s.address for s in chosen}
        write_report(args.report, results, [s for s in rows if s.address not in chosen_ids])

        counts = {}
        for _, r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        print("\nSummary: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        if counts.get("manual") or counts.get("failed") or counts.get("visited"):
            print(f"Open {args.report} to finish the remaining ones by hand.")
        print("Note: senders can legally take a few days to stop (UK PECR/GDPR allows reasonable time).")
        return 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass
