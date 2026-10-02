# inbox-unsub

A small tool that scans your inbox for marketing emails, shows you who emails
you daily, weekly, and so on, and unsubscribes you from the ones you choose,
in bulk.

**It runs on your own computer.** Your password and your emails never go to
anyone except your own mail provider. That is why it works in the UK: services
like Unroll.me stopped serving UK and EU users because they send your inbox to
their servers. This tool doesn't, so there's nothing for GDPR to block.

It needs only Python 3.8 or newer. There is nothing else to install.

## Quick start

```bash
git clone <this repo> && cd A
python3 -m inbox_unsub you@gmail.com
```

It will:

1. Ask for your password. **Use an app password** (see below), not your normal one.
2. Read the *headers only* of the last 90 days of email in your inbox.
3. Show a table like this:

```
  #  Emails  How often     Method     Sender
---  ------  ------------  ---------  ----------------------------------------
  1      30  daily         one-click  Boots <offers@boots.com>
  2      10  several/week  email      ASOS <news@asos.com>
  3       6  weekly        link       Rightmove <alerts@rightmove.co.uk>
```

4. Ask which ones to unsubscribe from: type `1,3`, `2-5`, `all`, or press Enter to quit.
5. Confirm, unsubscribe, and write `unsubscribe-report.html` with a link for
   anything that still needs a click from you.

Optionally, install it as a command:

```bash
pip install .
inbox-unsub you@gmail.com
```

## Getting an app password

| Provider | `--provider` | How |
|---|---|---|
| Gmail / Googlemail | `gmail` | Turn on 2-Step Verification, then create one at https://myaccount.google.com/apppasswords |
| Yahoo (also Sky accounts on Yahoo) | `yahoo` | Account Info → Account Security → Generate app password |
| iCloud | `icloud` | https://account.apple.com → Sign-In and Security → App-Specific Passwords |
| AOL | `aol` | Account Security → Generate app password |
| BT (btinternet.com) | `bt` | Your BT email password, or a "secure password" from My BT if you have 2-step verification |
| Sky (sky.com) | `sky` | Your Sky email password |
| Virgin Media | `virgin` | Your Virgin Media mail password (IMAP must be enabled in mail settings) |
| Outlook / Hotmail / Live | `outlook` | See below |

The provider is guessed from your address (gmail.com, hotmail.co.uk,
btinternet.com, and so on). For any other provider, pass `--imap-host` and
`--smtp-host`.

To avoid typing the password, set `INBOX_UNSUB_PASSWORD` in your environment.
The tool never saves it.

### Outlook, Hotmail and Live

Microsoft has switched off plain password logins for IMAP on personal
accounts, so you need an OAuth2 access token with the
`https://outlook.office.com/IMAP.AccessAsUser.All` and
`https://outlook.office.com/SMTP.Send` scopes. Pass it with
`--oauth-token <token>`. A simpler option is to forward or import your Outlook
mail into a Gmail account and run the tool there.

## How it unsubscribes

Legitimate bulk senders put a `List-Unsubscribe` header on their emails.
Gmail and Yahoo have required this from bulk senders since 2024. The tool tries
these methods, strongest first:

1. **one-click**: sends the standard RFC 8058 "One-Click" request. This is
   definitive and is what Gmail's own unsubscribe button uses.
2. **email**: sends an unsubscribe email from your account to the address the
   sender gave.
3. **link**: opens the unsubscribe link. Some senders then want you to press a
   "confirm" button. Those are marked `visited` in the report so you can check.
4. **manual**: a link found in the body (with `--deep`). It is never clicked
   automatically. It goes in the report for you to click.

Senders you've handled are remembered in `~/.inbox_unsub/state.json`, so the
next run only shows new ones.

## Useful options

```bash
# Look back a year, only daily or weekly senders
python3 -m inbox_unsub you@gmail.com --days 365 --frequency weekly

# Never touch these (part of an address or a domain)
python3 -m inbox_unsub you@gmail.com --keep mybank.co.uk --keep nhs.uk

# Preview without changing anything
python3 -m inbox_unsub you@gmail.com --all --dry-run

# Unsubscribe from everything listed and move their old emails out of the inbox
python3 -m inbox_unsub you@gmail.com --all --move-to "Unsubscribed"

# Also scan other folders, and look inside emails that lack the header
python3 -m inbox_unsub you@gmail.com --folder INBOX --folder "[Gmail]/Spam" --deep
```

Run `python3 -m inbox_unsub --help` for everything.

## Safety notes

- Scanning is read-only. Nothing in your mailbox changes unless you use `--move-to`.
- Senders whose DKIM signature **failed** are hidden by default
  (`--include-unverified` shows them). A failed signature often means a forged
  sender, and clicking "unsubscribe" in real spam only confirms that your
  address is live. Mark real spam as spam instead.
- Under UK PECR/GDPR rules, a sender must stop marketing to you once you opt
  out, but they may take a few days.

## Development

```bash
python3 -m unittest discover -s tests -v
```
