from fastapi.testclient import TestClient
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

from app.core.config import Settings
from app.main import app
from app.security import ldap_auth
from app.security.ldap_auth import authenticate as real_authenticate  # before the autouse fake
from app.security.rbac import Permission, permissions_for
from app.security.sessions import COOKIE_NAME


def test_requests_without_a_session_are_refused():
    client = TestClient(app)
    for url in ("/api/overview", "/api/emails/queue", "/api/topics", "/api/settings", "/api/admin/users"):
        assert client.get(url).status_code == 401, url


def test_login_sets_httponly_strict_cookie_and_me_works(login):
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"username": "Olivia", "password": "pw"})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert COOKIE_NAME in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    me = client.get("/api/auth/me").json()
    assert me["user"]["username"] == "olivia"
    assert me["user"]["role"] == "officer"
    assert "topics:edit" not in me["permissions"]


def test_wrong_password_and_lockout(login):
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "sam", "password": "nope"}).status_code == 401
    # Even the right password is refused while locked out.
    assert client.post("/api/auth/login", json={"username": "sam", "password": "pw"}).status_code == 429


def test_changes_without_csrf_token_are_refused(login):
    api = login("adam")
    resp = api.c.put("/api/settings", json={})  # no X-CSRF-Token header
    assert resp.status_code == 403


def test_logout_ends_the_session(login):
    api = login("olivia")
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get("/api/auth/me").status_code == 401


def test_four_roles_officer_supervisor_auditor_admin():
    officer, supervisor = permissions_for("officer"), permissions_for("supervisor")
    auditor, admin = permissions_for("auditor"), permissions_for("admin")
    assert officer < supervisor < admin
    assert auditor < admin
    assert Permission.EDIT_SETTINGS in admin and Permission.EDIT_SETTINGS not in officer
    assert Permission.EDIT_KNOWLEDGE not in officer and Permission.VIEW_KNOWLEDGE in officer
    assert Permission.SEND_REPLIES in officer and Permission.APPROVE_REPLIES not in officer
    assert Permission.APPROVE_REPLIES in supervisor and Permission.MANAGE_AUTOMATION not in supervisor
    # The auditor sees everything needed to reconstruct decisions, and changes nothing.
    assert Permission.VIEW_AUDIT in auditor and Permission.VIEW_AI in auditor
    assert not auditor & {
        Permission.HANDLE_QUEUE,
        Permission.SEND_REPLIES,
        Permission.EDIT_SETTINGS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_AUTOMATION,
        Permission.HANDLE_CASES,
        Permission.EDIT_KNOWLEDGE,
    }
    assert permissions_for("nobody") == frozenset()


def test_endpoints_enforce_permissions(login):
    officer, admin = login("olivia"), login("adam")
    settings_body = admin.get("/api/settings").json()
    settings_body = {
        k: settings_body[k]
        for k in (
            "ack_enabled",
            "auto_reply_enabled",
            "confidence_threshold",
            "ack_template",
            "answer_header",
            "answer_footer",
            "sensitive_keywords",
        )
    }
    assert officer.get("/api/settings").status_code == 403
    assert officer.get("/api/admin/audit").status_code == 403
    assert officer.get("/api/emails/export.pdf").status_code == 403
    assert officer.post("/api/test/match", json={"body": "hi", "run_query": True}).status_code == 403
    assert (
        officer.post(
            "/api/knowledge",
            data={"name": "x", "category": "General"},
            files={"file": ("x.md", b"Q: a?\nA: b", "text/markdown")},
        ).status_code
        == 403
    )
    assert admin.put("/api/settings", json=settings_body).status_code == 200
    assert admin.get("/api/admin/users").status_code == 200


def _mock_directory():
    server = Server("ad.fnb.test", get_info=OFFLINE_AD_2012_R2)
    conn = Connection(server, user="CN=svc,DC=fnb,DC=test", password="svc-pw", client_strategy=MOCK_SYNC)
    conn.strategy.add_entry("CN=svc,DC=fnb,DC=test", {"userPassword": "svc-pw", "sAMAccountName": "svc"})
    conn.strategy.add_entry(
        "CN=Kwame,OU=Staff,DC=fnb,DC=test",
        {
            "objectClass": "user",
            "sAMAccountName": "kwame",
            "userPassword": "secret",
            "displayName": "Kwame Asante",
            "mail": "kwame@fnb.test",
            "memberOf": [
                "CN=Finance,OU=Groups,DC=fnb,DC=test",
                "CN=EmailCare-Users,OU=Groups,DC=fnb,DC=test",
            ],
        },
    )
    conn.strategy.add_entry(
        "CN=Efua,OU=Staff,DC=fnb,DC=test",
        {
            "objectClass": "user",
            "sAMAccountName": "efua",
            "userPassword": "secret",
            "memberOf": ["CN=Finance,OU=Groups,DC=fnb,DC=test"],
        },
    )
    return server, conn


def test_real_ldap_flow_against_mock_directory(monkeypatch):
    server, conn = _mock_directory()
    # Make every Connection the module opens use the mock directory.
    monkeypatch.setattr(
        ldap_auth,
        "Connection",
        lambda srv, user=None, password=None, **kw: Connection(
            server, user=user, password=password, client_strategy=MOCK_SYNC
        ),
    )
    # The mock strategy shares entries per server object, so seed via the first connection.
    conn.bind()
    settings = Settings(
        env="test",
        ldap_url="ldap://ad.fnb.test",
        ldap_bind_dn="CN=svc,DC=fnb,DC=test",
        ldap_bind_password="svc-pw",
        ldap_user_search_base="DC=fnb,DC=test",
        ldap_group="cn=emailcare-users,ou=groups,dc=fnb,dc=test",  # case does not matter
    )
    user = real_authenticate("kwame", "secret", settings, server=server)
    assert user.username == "kwame"
    assert user.display_name == "Kwame Asante"

    for username, password, message in [
        ("kwame", "wrong", ldap_auth.INVALID_CREDENTIALS),
        ("kwame", "", ldap_auth.INVALID_CREDENTIALS),  # empty password never reaches AD
        ("nobody", "secret", ldap_auth.INVALID_CREDENTIALS),
        ("efua", "secret", ldap_auth.NOT_AUTHORISED),  # right password, not in the group
        ("*)(objectClass=*", "x", ldap_auth.INVALID_CREDENTIALS),  # filter injection is escaped
    ]:
        try:
            real_authenticate(username, password, settings, server=server)
            raise AssertionError(f"{username} should have been refused")
        except ldap_auth.AuthError as exc:
            assert str(exc) == message


def test_production_refuses_unsafe_configuration():
    import pytest

    with pytest.raises(ValueError, match="Unsafe production configuration"):
        Settings(env="production", session_secret="short", ldap_url="ldap://plain", cookie_secure=False)


def test_first_sign_in_is_officer_and_bootstrap_admin_is_admin():
    client = TestClient(app)
    me = client.post("/api/auth/login", json={"username": "olivia", "password": "pw"}).json()
    assert me["user"]["role"] == "officer"
    me = TestClient(app).post("/api/auth/login", json={"username": "adam", "password": "pw"}).json()
    assert me["user"]["role"] == "admin"


def test_admin_changes_role_and_it_applies_immediately(login):
    officer, admin = login("olivia"), login("adam")
    olivia_id = officer.get("/api/auth/me").json()["user"]["id"]
    assert officer.get("/api/settings").status_code == 403

    resp = admin.c.patch(
        f"/api/admin/users/{olivia_id}", json={"role": "admin"}, headers={"X-CSRF-Token": admin.csrf}
    )
    assert resp.status_code == 200 and resp.json()["role"] == "admin"
    # Her old session was ended; after signing in again she has admin rights.
    assert officer.get("/api/auth/me").status_code == 401
    assert login("olivia").get("/api/settings").status_code == 200


def test_disabled_user_cannot_sign_in(login):
    officer, admin = login("olivia"), login("adam")
    olivia_id = officer.get("/api/auth/me").json()["user"]["id"]
    admin.c.patch(
        f"/api/admin/users/{olivia_id}", json={"is_active": False}, headers={"X-CSRF-Token": admin.csrf}
    )
    assert officer.get("/api/auth/me").status_code == 401
    resp = TestClient(app).post("/api/auth/login", json={"username": "olivia", "password": "pw"})
    assert resp.status_code == 403


def test_admin_cannot_change_own_role_and_others_cannot_manage_users(login):
    admin, supervisor = login("adam"), login("olivia")  # an officer cannot manage users
    adam_id = admin.get("/api/auth/me").json()["user"]["id"]
    resp = admin.c.patch(
        f"/api/admin/users/{adam_id}", json={"role": "officer"}, headers={"X-CSRF-Token": admin.csrf}
    )
    assert resp.status_code == 409
    resp = supervisor.c.patch(
        f"/api/admin/users/{adam_id}", json={"role": "officer"}, headers={"X-CSRF-Token": supervisor.csrf}
    )
    assert resp.status_code == 403


def test_dev_sign_in_is_refused_outside_development():
    import pytest

    for env in ("production", "test"):
        with pytest.raises(ValueError, match="only allowed when FNB_ENV=development"):
            Settings(env=env, auth_mode="dev")


def test_dev_sign_in_accepts_any_password_in_development():
    from app.core.config import get_settings

    dev = Settings(
        env="development",
        auth_mode="dev",
        session_secret="x" * 40,
        cookie_secure=False,
        bootstrap_admins="admin",
    )
    app.dependency_overrides[get_settings] = lambda: dev
    try:
        client = TestClient(app)
        assert client.get("/api/auth/mode").json() == {"mode": "dev"}
        me = client.post("/api/auth/login", json={"username": "F12345", "password": "anything"}).json()
        assert me["user"]["username"] == "f12345" and me["user"]["role"] == "officer"
        me = TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "x"}).json()
        assert me["user"]["role"] == "admin"
        bad = TestClient(app).post("/api/auth/login", json={"username": "bad name!", "password": "x"})
        assert bad.status_code == 401
    finally:
        app.dependency_overrides.pop(get_settings, None)
