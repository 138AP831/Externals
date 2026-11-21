"""Live Gmail tools over IMAP (app password).

``gmail_search_emails`` / ``gmail_list_unread`` / ``gmail_read_email`` query
the mailbox directly on each call, so they need no prior sync or index and keep
working on hosts whose disk is wiped on restart.

Credentials come from ``GMAIL_ADDRESS`` + ``GMAIL_APP_PASSWORD`` environment
variables, falling back to a connection saved by the ``gmail_imap`` connector
(``jarvis connect gmail_imap`` or the web UI). The mailbox is always opened
read-only: these tools never change, move or mark mail.
"""

from __future__ import annotations

import email as email_lib
import imaplib
import os
import re
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_RESULTS = 25
_BODY_PREVIEW_CHARS = 300
_BODY_FULL_CHARS = 8000


def _connector():
    """Return a GmailIMAPConnector using env credentials if present."""
    from openjarvis.connectors.gmail_imap import GmailIMAPConnector

    address = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if address and password:
        return GmailIMAPConnector(email_address=address, app_password=password)
    return GmailIMAPConnector()


def _not_configured(tool: str) -> ToolResult:
    return ToolResult(
        tool_name=tool,
        content=(
            "Gmail is not connected. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
            "(a Google App Password from https://myaccount.google.com/apppasswords) "
            "or run `jarvis connect gmail_imap`."
        ),
        success=False,
    )


def _has_credentials(conn: Any) -> bool:
    em, pw, host, _, _, _ = conn._resolve_connection()
    return bool(em and pw and host)


def _open_all_mail(conn: Any) -> imaplib.IMAP4:
    """Open IMAP and select Gmail's All Mail (falls back to INBOX), read-only."""
    imap = conn._open_imap()  # selects INBOX read-only
    status, _ = imap.select('"[Gmail]/All Mail"', readonly=True)
    if status != "OK":
        imap.select("INBOX", readonly=True)
    return imap


def _summaries(
    imap: imaplib.IMAP4, uids: List[bytes], max_results: int, with_preview: bool
) -> List[Dict[str, str]]:
    from openjarvis.connectors.gmail_imap import (
        _decode_header,
        _extract_text_body,
        _parse_date,
    )

    out: List[Dict[str, str]] = []
    # Newest first: IMAP returns ascending UIDs.
    for uid in list(reversed(uids))[:max_results]:
        what = "(BODY.PEEK[])" if with_preview else "(BODY.PEEK[HEADER])"
        status, data = imap.uid("FETCH", uid, what)
        if status != "OK" or not data or not isinstance(data[0], tuple):
            continue
        msg = email_lib.message_from_bytes(data[0][1])
        item = {
            "id": uid.decode(),
            "date": _parse_date(msg).strftime("%Y-%m-%d %H:%M"),
            "from": _decode_header(msg.get("From", "")),
            "subject": _decode_header(msg.get("Subject", "")) or "(no subject)",
        }
        if with_preview:
            body = re.sub(r"\s+", " ", _extract_text_body(msg)).strip()
            item["preview"] = body[:_BODY_PREVIEW_CHARS]
        out.append(item)
    return out


def _format(items: List[Dict[str, str]], header: str) -> str:
    if not items:
        return f"{header}: none found."
    lines = [f"{header} ({len(items)}):"]
    for it in items:
        lines.append(
            f"- [id {it['id']}] {it['date']} | From: {it['from']} | "
            f"Subject: {it['subject']}"
        )
        if it.get("preview"):
            lines.append(f"    {it['preview']}")
    return "\n".join(lines)


def _clamp(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, _MAX_RESULTS))


def _run(tool: str, fn) -> ToolResult:
    conn = _connector()
    if not _has_credentials(conn):
        return _not_configured(tool)
    imap: Optional[imaplib.IMAP4] = None
    try:
        imap = _open_all_mail(conn)
        return ToolResult(tool_name=tool, content=fn(imap))
    except imaplib.IMAP4.error as exc:
        return ToolResult(
            tool_name=tool,
            content=(
                f"Gmail login/search failed: {exc}. Check the address and app "
                "password, and that IMAP is enabled in Gmail settings."
            ),
            success=False,
        )
    except Exception as exc:  # network, timeout, TLS
        return ToolResult(tool_name=tool, content=f"Gmail error: {exc}", success=False)
    finally:
        conn._close_imap(imap)


@ToolRegistry.register("gmail_search_emails")
class GmailSearchTool(BaseTool):
    """Search the user's Gmail with Gmail search syntax."""

    tool_id = "gmail_search_emails"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_search_emails",
            description=(
                "Search the user's Gmail. `query` uses Gmail search syntax, e.g. "
                "'from:amazon newer_than:7d', 'subject:invoice', 'is:unread', "
                "'has:attachment flight'. Returns newest matches with sender, "
                "subject, date, a short preview and an id for gmail_read_email."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query"},
                    "max_results": {
                        "type": "integer",
                        "description": "How many emails (default 10, max 25)",
                    },
                },
                "required": ["query"],
            },
            category="communication",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "")).strip() or "in:inbox"
        n = _clamp(params.get("max_results"), 10)

        def fn(imap: imaplib.IMAP4) -> str:
            status, data = imap.uid(
                "SEARCH", "X-GM-RAW", f'"{query.replace(chr(34), "")}"'
            )
            uids = data[0].split() if status == "OK" and data and data[0] else []
            return _format(
                _summaries(imap, uids, n, True), f"Emails matching '{query}'"
            )

        return _run(self.tool_id, fn)


@ToolRegistry.register("gmail_list_unread")
class GmailUnreadTool(BaseTool):
    """List recent unread emails in the inbox."""

    tool_id = "gmail_list_unread"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_list_unread",
            description="List the user's most recent unread Gmail inbox emails.",
            parameters={
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "How many emails (default 10, max 25)",
                    },
                },
            },
            category="communication",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        n = _clamp(params.get("max_results"), 10)

        def fn(imap: imaplib.IMAP4) -> str:
            status, data = imap.uid("SEARCH", "X-GM-RAW", '"in:inbox is:unread"')
            uids = data[0].split() if status == "OK" and data and data[0] else []
            return _format(_summaries(imap, uids, n, True), "Unread emails")

        return _run(self.tool_id, fn)


@ToolRegistry.register("gmail_read_email")
class GmailReadTool(BaseTool):
    """Read one email in full by the id returned from search/unread."""

    tool_id = "gmail_read_email"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="gmail_read_email",
            description=(
                "Read the full text of one email, by the id shown in "
                "gmail_search_emails or gmail_list_unread results."
            ),
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "Email id"}},
                "required": ["id"],
            },
            category="communication",
            timeout_seconds=60.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        uid = str(params.get("id", "")).strip()
        if not uid.isdigit():
            return ToolResult(
                tool_name=self.tool_id, content="Invalid email id.", success=False
            )

        def fn(imap: imaplib.IMAP4) -> str:
            from openjarvis.connectors.gmail_imap import (
                _decode_header,
                _extract_text_body,
                _parse_date,
            )

            status, data = imap.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                return f"No email with id {uid}."
            msg = email_lib.message_from_bytes(data[0][1])
            body = _extract_text_body(msg)
            if "<html" in body[:500].lower() or "<div" in body[:500].lower():
                body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"[ \t]+", " ", body)
            body = re.sub(r"\n\s*\n+", "\n\n", body).strip()
            return (
                f"From: {_decode_header(msg.get('From', ''))}\n"
                f"To: {_decode_header(msg.get('To', ''))}\n"
                f"Date: {_parse_date(msg).strftime('%Y-%m-%d %H:%M')}\n"
                f"Subject: {_decode_header(msg.get('Subject', ''))}\n\n"
                f"{body[:_BODY_FULL_CHARS]}"
            )

        return _run(self.tool_id, fn)
