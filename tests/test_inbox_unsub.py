import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from inbox_unsub import cli, headers, scanner, unsubscriber
from inbox_unsub.headers import utcnow


def raw_message(sender, days_ago, unsub=None, post=None, subject="Big sale!", dkim="pass"):
    date = (utcnow() - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    lines = [f"From: {sender}", f"Date: {date}", f"Subject: {subject}",
             f"Authentication-Results: mx.example; dkim={dkim} header.d=example.com"]
    if unsub:
        lines.append(f"List-Unsubscribe: {unsub}")
    if post:
        lines.append(f"List-Unsubscribe-Post: {post}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class FakeIMAP:
    def __init__(self, messages):
        self.messages = {str(i): m for i, m in enumerate(messages, 1)}
        self.moved = []

    def select(self, mailbox, readonly=False):
        return "OK", [str(len(self.messages)).encode()]

    def capability(self):
        return "OK", [b"IMAP4rev1 MOVE UIDPLUS"]

    def create(self, mailbox):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(self.messages).encode()]
        if command == "FETCH":
            out = []
            for uid in args[0].split(","):
                out.append((f"{uid} (UID {uid} BODY[HEADER] {{1}}".encode(), self.messages[uid]))
                out.append(b")")
            return "OK", out
        if command == "MOVE":
            self.moved.extend(args[0].split(","))
            return "OK", [b""]
        raise AssertionError(command)


class HeaderTests(unittest.TestCase):
    def test_parse_list_unsubscribe(self):
        http, mailto = headers.parse_list_unsubscribe(
            "<mailto:unsub@shop.co.uk?subject=stop>,\r\n <https://shop.co.uk/u?\r\n id=1>")
        self.assertEqual(http, ["https://shop.co.uk/u?id=1"])
        self.assertEqual(mailto, ["mailto:unsub@shop.co.uk?subject=stop"])

    def test_one_click(self):
        self.assertTrue(headers.is_one_click("List-Unsubscribe=One-Click"))
        self.assertFalse(headers.is_one_click(""))

    def test_encoded_sender(self):
        name, addr = headers.parse_sender("=?UTF-8?B?w4Fydm9z?= <News@Example.COM>")
        self.assertEqual((name, addr), ("Árvos", "news@example.com"))

    def test_body_links(self):
        body = ('<a href="https://x.com/home">Home</a>'
                '<a href="https://x.com/p?a=1&amp;b=2"><span>Unsubscribe</span></a>')
        self.assertEqual(headers.find_unsubscribe_links(body), ["https://x.com/p?a=1&b=2"])

    def test_dkim(self):
        self.assertTrue(headers.dkim_status("mx; dkim=pass header.d=a"))
        self.assertFalse(headers.dkim_status("mx; dkim=fail"))
        self.assertIsNone(headers.dkim_status(""))

    def test_mailto(self):
        self.assertEqual(unsubscriber.parse_mailto("mailto:a@b.com?subject=Remove%20me"),
                         ("a@b.com", "Remove me", "unsubscribe"))

    def test_selection(self):
        self.assertEqual(cli.parse_selection("1, 3-4", 5), [0, 2, 3])
        self.assertEqual(cli.parse_selection("all", 3), [0, 1, 2])
        with self.assertRaises(ValueError):
            cli.parse_selection("9", 3)


class ScannerTests(unittest.TestCase):
    def test_grouping_and_frequency(self):
        msgs = [raw_message("Shop <deals@shop.co.uk>", d, "<https://shop.co.uk/u/%d>" % d,
                            "List-Unsubscribe=One-Click") for d in range(10)]
        msgs += [raw_message("Weekly <news@paper.com>", d, "<mailto:u@paper.com>") for d in (0, 7, 14)]
        msgs += [raw_message("Mum <mum@family.net>", 1)]
        senders = {}
        scanner.scan_folder(FakeIMAP(msgs), "INBOX", 90, senders)
        shop = senders["deals@shop.co.uk"]
        self.assertEqual((shop.count, shop.frequency, shop.method), (10, "daily", "one-click"))
        self.assertEqual(shop.http_urls, ["https://shop.co.uk/u/0"])  # newest link wins
        self.assertEqual(senders["news@paper.com"].frequency, "weekly")
        self.assertEqual(senders["news@paper.com"].method, "email")
        self.assertFalse(senders["mum@family.net"].can_unsubscribe)


class Handler(BaseHTTPRequestHandler):
    hits = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.hits.append(("POST", self.path, body))
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        Handler.hits.append(("GET", self.path, b""))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        Handler.hits = []
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_full_run(self):
        msgs = [raw_message("Shop <deals@shop.co.uk>", d, f"<{self.base}/oneclick>",
                            "List-Unsubscribe=One-Click") for d in range(5)]
        msgs += [raw_message("Blog <hi@blog.com>", d, f"<{self.base}/visit>") for d in (0, 7)]
        msgs += [raw_message("Bank <alerts@mybank.co.uk>", d, f"<{self.base}/bank>") for d in (0, 1)]
        msgs += [raw_message("Fake <x@spoof.com>", d, f"<{self.base}/spoof>", dkim="fail") for d in (0, 1)]
        fake = FakeIMAP(msgs)
        report = os.path.join(self.tmp.name, "r.html")
        with mock.patch.object(scanner, "connect", return_value=fake), \
             mock.patch.object(cli, "STATE_DIR", cli.Path(self.tmp.name)), \
             mock.patch.object(cli.time, "sleep"), \
             mock.patch.dict(os.environ, {"INBOX_UNSUB_PASSWORD": "x"}), \
             redirect_stdout(io.StringIO()) as out:
            code = cli.main(["me@gmail.com", "--all", "--yes", "--keep", "mybank",
                             "--move-to", "Unsubscribed", "--report", report])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn(("POST", "/oneclick", b"List-Unsubscribe=One-Click"), Handler.hits)
        self.assertIn(("GET", "/visit", b""), Handler.hits)
        paths = {p for _, p, _ in Handler.hits}
        self.assertNotIn("/bank", paths)     # --keep respected
        self.assertNotIn("/spoof", paths)    # failed DKIM skipped
        self.assertEqual(len(fake.moved), 7)  # 5 shop + 2 blog
        self.assertTrue(os.path.exists(report))

        # Second run should skip senders already handled.
        Handler.hits = []
        with mock.patch.object(scanner, "connect", return_value=FakeIMAP(msgs)), \
             mock.patch.object(cli, "STATE_DIR", cli.Path(self.tmp.name)), \
             mock.patch.object(cli.time, "sleep"), \
             mock.patch.dict(os.environ, {"INBOX_UNSUB_PASSWORD": "x"}), \
             redirect_stdout(io.StringIO()):
            cli.main(["me@gmail.com", "--all", "--yes", "--keep", "mybank", "--report", report])
        self.assertEqual(Handler.hits, [])

    def test_dry_run_sends_nothing(self):
        msgs = [raw_message("Shop <deals@shop.co.uk>", d, f"<{self.base}/oneclick>",
                            "List-Unsubscribe=One-Click") for d in range(3)]
        with mock.patch.object(scanner, "connect", return_value=FakeIMAP(msgs)), \
             mock.patch.object(cli, "STATE_DIR", cli.Path(self.tmp.name)), \
             mock.patch.dict(os.environ, {"INBOX_UNSUB_PASSWORD": "x"}), \
             redirect_stdout(io.StringIO()):
            cli.main(["me@gmail.com", "--all", "--yes", "--dry-run",
                      "--report", os.path.join(self.tmp.name, "r.html")])
        self.assertEqual(Handler.hits, [])


if __name__ == "__main__":
    unittest.main()
