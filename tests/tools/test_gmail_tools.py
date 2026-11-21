"""Tests for the live Gmail IMAP tools (no network)."""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from openjarvis.tools import gmail_tools


def _raw(uid: int, subject: str, sender: str, body: str) -> bytes:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "me@gmail.com"
    m["Subject"] = subject
    m["Date"] = f"Mon, 0{uid} Jun 2026 10:00:00 +0000"
    m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, mails: dict[int, bytes]):
        self.mails = mails
        self.searches: list[str] = []
        self.selected: list[tuple] = []

    def select(self, box, readonly=False):
        self.selected.append((box, readonly))
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            self.searches.append(args[1])
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.mails))]
        if cmd == "FETCH":
            uid = int(args[0])
            if uid not in self.mails:
                return "OK", [None]
            return "OK", [(b"hdr", self.mails[uid])]
        raise AssertionError(cmd)

    def logout(self):
        return "BYE", []


@pytest.fixture()
def fake(monkeypatch):
    imap = FakeIMAP(
        {
            1: _raw(1, "Old invoice", "Shop <shop@example.com>", "Pay 10 dollars"),
            2: _raw(2, "Flight booked", "Air <air@example.com>", "Seat 12A confirmed"),
        }
    )
    monkeypatch.setenv("GMAIL_ADDRESS", "me@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
    from openjarvis.connectors.gmail_imap import GmailIMAPConnector

    monkeypatch.setattr(GmailIMAPConnector, "_open_imap", lambda self: imap)
    return imap


def test_search_newest_first_with_preview(fake):
    res = gmail_tools.GmailSearchTool().execute(query="from:air newer_than:7d")
    assert res.success
    assert res.content.index("Flight booked") < res.content.index("Old invoice")
    assert "Seat 12A confirmed" in res.content
    assert fake.searches == ['"from:air newer_than:7d"']
    # Always read-only.
    assert all(ro for _, ro in fake.selected)


def test_unread_and_max_results(fake):
    res = gmail_tools.GmailUnreadTool().execute(max_results=1)
    assert res.success
    assert "Flight booked" in res.content and "Old invoice" not in res.content
    assert fake.searches == ['"in:inbox is:unread"']


def test_read_email(fake):
    res = gmail_tools.GmailReadTool().execute(id="1")
    assert "Subject: Old invoice" in res.content
    assert "Pay 10 dollars" in res.content
    assert not gmail_tools.GmailReadTool().execute(id="1; DROP").success


def test_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    from openjarvis.connectors import gmail_imap

    monkeypatch.setattr(
        gmail_imap, "_DEFAULT_CREDENTIALS_PATH", str(tmp_path / "none.json")
    )
    res = gmail_tools.GmailUnreadTool().execute()
    assert not res.success and "not connected" in res.content


def test_registered_as_builtin_module():
    from openjarvis import tools

    assert "gmail_tools" in tools._BUILTIN_TOOL_MODULES
    for cls in (
        gmail_tools.GmailSearchTool,
        gmail_tools.GmailUnreadTool,
        gmail_tools.GmailReadTool,
    ):
        assert cls().spec.name == cls.tool_id
