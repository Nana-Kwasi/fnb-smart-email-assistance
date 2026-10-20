"""Sign-in against FNB's Active Directory over LDAPS.

Flow:
  1. Bind with the read-only service account and look the user up by username.
  2. Bind again as that user with the password they typed. Only AD ever sees the password;
     it is never stored or logged here.
  3. Check they are in the configured AD group. Anyone outside it is refused, even with a
     correct password. What they may do once inside (their role) is managed in the dashboard.
"""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass

from ldap3 import ALL_ATTRIBUTES, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import Settings

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Sign-in failed. The message is safe to show to the user."""


class DirectoryUnavailable(Exception):
    """AD could not be reached. Not the user's fault, and not a failed attempt."""


@dataclass(frozen=True)
class DirectoryUser:
    username: str
    display_name: str
    email: str | None


INVALID_CREDENTIALS = "Incorrect username or password."
NOT_AUTHORISED = (
    "Your account is not authorised to use this system. Contact IT to be added to the Email Care group."
)


def _server(settings: Settings) -> Server:
    tls = None
    if settings.ldap_url.lower().startswith("ldaps://"):
        tls = Tls(
            validate=ssl.CERT_REQUIRED,
            version=ssl.PROTOCOL_TLS_CLIENT,
            ca_certs_file=settings.ldap_ca_cert_file or None,
        )
    return Server(
        settings.ldap_url, use_ssl=tls is not None, tls=tls, connect_timeout=settings.ldap_timeout_seconds
    )


def in_access_group(groups: list[str], settings: Settings) -> bool:
    wanted = settings.ldap_group.strip().lower()
    return bool(wanted) and any(g.strip().lower() == wanted for g in groups)


def authenticate(
    username: str, password: str, settings: Settings, server: Server | None = None
) -> DirectoryUser:
    username = username.strip()
    # An empty password makes many directories perform an "unauthenticated bind" that succeeds.
    if not username or not password:
        raise AuthError(INVALID_CREDENTIALS)

    server = server or _server(settings)
    try:
        with Connection(
            server,
            user=settings.ldap_bind_dn,
            password=settings.ldap_bind_password,
            auto_bind=True,
            receive_timeout=settings.ldap_timeout_seconds,
        ) as svc:
            search_filter = settings.ldap_user_filter.format(username=escape_filter_chars(username))
            svc.search(
                settings.ldap_user_search_base,
                search_filter,
                SUBTREE,
                attributes=ALL_ATTRIBUTES,
                size_limit=2,
            )
            entries = list(svc.entries)
    except LDAPException as exc:
        log.error("LDAP service bind/search failed: %s", type(exc).__name__)
        raise DirectoryUnavailable from exc

    if len(entries) != 1:
        raise AuthError(INVALID_CREDENTIALS)
    entry = entries[0]
    user_dn = entry.entry_dn

    try:
        user_conn = Connection(
            server, user=user_dn, password=password, receive_timeout=settings.ldap_timeout_seconds
        )
        ok = user_conn.bind()
        user_conn.unbind()
    except LDAPException as exc:
        log.error("LDAP user bind failed: %s", type(exc).__name__)
        raise DirectoryUnavailable from exc
    if not ok:
        raise AuthError(INVALID_CREDENTIALS)

    attrs = entry.entry_attributes_as_dict
    if not in_access_group([str(g) for g in attrs.get("memberOf", [])], settings):
        raise AuthError(NOT_AUTHORISED)

    def first(name: str) -> str | None:
        values = attrs.get(name) or []
        return str(values[0]) if values else None

    return DirectoryUser(
        username=username.lower(),
        display_name=first("displayName") or first("cn") or username,
        email=first("mail"),
    )
