"""Microsoft Graph client for the one customer care mailbox.

Uses the app-only (client credentials) flow. Permissions needed on the Entra app registration:
``Mail.Read`` and ``Mail.Send`` (application). Limit them to the customer care mailbox with an
Exchange application access policy, so the app cannot read anyone else's mail.

Replies go through ``/messages/{id}/reply`` so they stay in the customer's email thread.
Alerts to the people in charge go through ``/sendMail``. Sent Items are read to notice when
someone has replied to a customer in Outlook, so the dashboard can mark it handled.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.services.attachments import RawAttachment

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 4
MESSAGE_FIELDS = (
    "id,subject,from,receivedDateTime,conversationId,body,internetMessageHeaders,webLink,hasAttachments"
)


class GraphError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class InboxMessage:
    id: str
    subject: str
    from_address: str
    from_name: str
    received_at: datetime
    conversation_id: str | None
    body_text: str
    headers: dict[str, str]
    web_link: str | None = None
    has_attachments: bool = False


@dataclass
class SentItem:
    sent_at: datetime
    preview: str


class GraphClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.graph_configured:
            raise GraphError("Microsoft Graph is not configured (FNB_GRAPH_* settings).")
        self._s = settings
        self._http = http or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expires = 0.0
        self._mailbox = quote(settings.graph_mailbox)

    # -- auth -------------------------------------------------------------------------------

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        resp = self._http.post(
            f"https://login.microsoftonline.com/{self._s.graph_tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._s.graph_client_id,
                "client_secret": self._s.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        if resp.status_code != 200:
            raise GraphError(f"Could not get a Microsoft Graph token (HTTP {resp.status_code}).")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    def _request(
        self, method: str, url: str, headers: dict[str, str] | None = None, **kwargs: Any
    ) -> httpx.Response:
        """Send a request, waiting and retrying when Microsoft asks us to slow down."""
        for attempt in range(MAX_RETRIES + 1):
            all_headers = {"Authorization": f"Bearer {self._access_token()}", **(headers or {})}
            resp = self._http.request(method, url, headers=all_headers, **kwargs)
            if resp.status_code in (429, 503, 504) and attempt < MAX_RETRIES:
                wait = min(int(resp.headers.get("Retry-After", 2**attempt)), 60)
                log.warning("Graph throttled (HTTP %s); retrying in %ss", resp.status_code, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 401 and attempt == 0:
                self._token = None  # token revoked or expired early; get a new one once
                continue
            if resp.status_code >= 400:
                raise GraphError(
                    f"Microsoft Graph returned HTTP {resp.status_code} for a {method} request.",
                    status=resp.status_code,
                )
            return resp
        raise GraphError("Microsoft Graph kept throttling; gave up for now.")

    # -- mailbox ----------------------------------------------------------------------------

    def inbox_changes(self, delta_link: str | None) -> tuple[list[str], str]:
        """IDs of messages added to the inbox since ``delta_link``, plus the next delta link.

        On the first run (no delta link) this walks the whole inbox once; the caller ignores
        anything received before it started.
        """
        url = delta_link or f"{GRAPH}/users/{self._mailbox}/mailFolders/inbox/messages/delta?$select=id"
        ids: list[str] = []
        while True:
            data = self._request("GET", url).json()
            ids.extend(item["id"] for item in data.get("value", []) if "@removed" not in item)
            if "@odata.nextLink" in data:
                url = data["@odata.nextLink"]
                continue
            return ids, data["@odata.deltaLink"]

    def get_message(self, message_id: str) -> InboxMessage:
        resp = self._request(
            "GET",
            f"{GRAPH}/users/{self._mailbox}/messages/{quote(message_id)}",
            params={"$select": MESSAGE_FIELDS},
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        m = resp.json()
        sender = (m.get("from") or {}).get("emailAddress") or {}
        return InboxMessage(
            id=m["id"],
            subject=m.get("subject") or "",
            from_address=(sender.get("address") or "").strip(),
            from_name=(sender.get("name") or "").strip(),
            received_at=datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00")),
            conversation_id=m.get("conversationId"),
            body_text=(m.get("body") or {}).get("content") or "",
            headers={h["name"]: h["value"] for h in m.get("internetMessageHeaders") or []},
            web_link=m.get("webLink"),
            has_attachments=bool(m.get("hasAttachments")),
        )

    def list_attachments(self, message_id: str, max_bytes: int) -> list[RawAttachment]:
        """File attachments; contents downloaded only when within ``max_bytes``."""
        base = f"{GRAPH}/users/{self._mailbox}/messages/{quote(message_id)}/attachments"
        data = self._request("GET", base, params={"$select": "id,name,contentType,size,isInline"}).json()
        out: list[RawAttachment] = []
        for a in data.get("value", []):
            is_file = a.get("@odata.type") == "#microsoft.graph.fileAttachment"
            content = None
            if is_file and int(a.get("size") or 0) <= max_bytes:
                full = self._request("GET", f"{base}/{quote(a['id'])}").json()
                content = base64.b64decode(full.get("contentBytes") or "")
            out.append(
                RawAttachment(
                    name=a.get("name") or "(no name)",
                    content_type=a.get("contentType") or "",
                    size=int(a.get("size") or 0),
                    data=content,
                    is_inline=bool(a.get("isInline")),
                )
            )
        return out

    def reply(self, message_id: str, html: str) -> None:
        self._request(
            "POST",
            f"{GRAPH}/users/{self._mailbox}/messages/{quote(message_id)}/reply",
            json={"message": {"body": {"contentType": "HTML", "content": html}}},
        )

    def send_mail(self, to: list[str], subject: str, html: str, cc: list[str] | None = None) -> None:
        self._request(
            "POST",
            f"{GRAPH}/users/{self._mailbox}/sendMail",
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html},
                    "toRecipients": [{"emailAddress": {"address": a}} for a in to],
                    "ccRecipients": [{"emailAddress": {"address": a}} for a in cc or []],
                },
                "saveToSentItems": False,
            },
        )

    def sent_in_conversation(self, conversation_id: str) -> list[SentItem]:
        escaped = conversation_id.replace("'", "''")
        data = self._request(
            "GET",
            f"{GRAPH}/users/{self._mailbox}/mailFolders/sentitems/messages",
            params={
                "$filter": f"conversationId eq '{escaped}'",
                "$select": "sentDateTime,bodyPreview",
                "$top": "50",
            },
        ).json()
        return [
            SentItem(
                sent_at=datetime.fromisoformat(m["sentDateTime"].replace("Z", "+00:00")),
                preview=m.get("bodyPreview") or "",
            )
            for m in data.get("value", [])
        ]
