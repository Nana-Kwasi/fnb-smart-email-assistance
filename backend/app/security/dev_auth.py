"""Development sign-in: any username and password, for trying the dashboard without LDAP.

Only reachable when FNB_AUTH_MODE=dev, which the settings refuse unless FNB_ENV=development.
"""

from __future__ import annotations

import re

from app.security.ldap_auth import INVALID_CREDENTIALS, AuthError, DirectoryUser

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def authenticate(username: str, password: str) -> DirectoryUser:
    username = username.strip().lower()
    if not password or not _USERNAME.fullmatch(username):
        raise AuthError(INVALID_CREDENTIALS)
    display = " ".join(part.capitalize() for part in re.split(r"[._-]+", username) if part)
    return DirectoryUser(username=username, display_name=display or username, email=None)
