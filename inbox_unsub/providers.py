"""IMAP/SMTP settings for common providers (including UK ISPs)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    imap_host: str
    smtp_host: str
    smtp_port: int = 465          # 465 = implicit TLS, 587 = STARTTLS
    imap_port: int = 993
    notes: str = ""


PROVIDERS = {
    "gmail": Provider("Gmail / Google Workspace", "imap.gmail.com", "smtp.gmail.com",
                      notes="Turn on 2-Step Verification, then create an App Password at "
                            "https://myaccount.google.com/apppasswords"),
    "outlook": Provider("Outlook / Hotmail / Live / Microsoft 365", "outlook.office365.com",
                        "smtp.office365.com", 587,
                        notes="Microsoft has switched off password logins for IMAP on most accounts. "
                              "Use --oauth-token with an OAuth2 access token (see README)."),
    "yahoo": Provider("Yahoo Mail", "imap.mail.yahoo.com", "smtp.mail.yahoo.com",
                      notes="Create an app password under Account Security."),
    "icloud": Provider("iCloud Mail", "imap.mail.me.com", "smtp.mail.me.com", 587,
                       notes="Create an app-specific password at https://account.apple.com"),
    "aol": Provider("AOL Mail", "imap.aol.com", "smtp.aol.com",
                    notes="Create an app password under Account Security."),
    "bt": Provider("BT Mail (btinternet.com)", "imap.mail.btinternet.com", "mail.btinternet.com"),
    "sky": Provider("Sky Mail (sky.com)", "imap.tools.sky.com", "smtp.tools.sky.com"),
    "virgin": Provider("Virgin Media Mail", "imap.virginmedia.com", "smtp.virginmedia.com"),
}

DOMAIN_HINTS = {
    "gmail.com": "gmail", "googlemail.com": "gmail",
    "outlook.com": "outlook", "hotmail.com": "outlook", "hotmail.co.uk": "outlook",
    "live.com": "outlook", "live.co.uk": "outlook", "msn.com": "outlook",
    "yahoo.com": "yahoo", "yahoo.co.uk": "yahoo", "ymail.com": "yahoo", "rocketmail.com": "yahoo",
    "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud",
    "aol.com": "aol", "aol.co.uk": "aol",
    "btinternet.com": "bt", "btopenworld.com": "bt", "talk21.com": "bt",
    "sky.com": "sky",
    "virginmedia.com": "virgin", "blueyonder.co.uk": "virgin", "ntlworld.com": "virgin",
}


def guess_provider(email_address: str):
    domain = email_address.rsplit("@", 1)[-1].lower()
    return DOMAIN_HINTS.get(domain)
