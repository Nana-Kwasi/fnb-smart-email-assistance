"""Application settings, read from environment variables (or a local .env file).

Everything sensitive (LDAP service password, Microsoft Graph secret, session key) comes from
the environment and nowhere else. In production the settings refuse to load if anything
looks unsafe, so a misconfigured server fails at startup rather than running insecurely.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FNB_", extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    # "ldap" signs in against Active Directory. "dev" accepts any username and password, for
    # trying the dashboard on a laptop; it is refused unless env is "development".
    auth_mode: Literal["ldap", "dev"] = "ldap"
    # Used for "today" on the dashboard. Any IANA zone name, e.g. Africa/Johannesburg.
    timezone: str = "UTC"

    # Database
    database_url: str = "postgresql+psycopg://fnb_care:fnb_care@localhost:5432/fnb_care"
    # Apply pending migrations when the API starts (never in tests, which build their own tables).
    auto_migrate: bool = True

    # Sessions
    session_secret: str = Field(default="", description="Long random string; used to hash session tokens")
    session_max_hours: int = 8
    cookie_secure: bool = True

    # Login lockout: this many failures for one username within the window locks it.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # LDAP / Active Directory
    ldap_url: str = "ldaps://ad.fnb.local:636"
    ldap_ca_cert_file: str | None = None
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_search_base: str = ""
    ldap_user_filter: str = "(&(objectClass=user)(sAMAccountName={username}))"
    # Members of this AD group may sign in. Roles are then managed in the dashboard.
    ldap_group: str = ""
    # Comma-separated usernames made admin on their first sign-in, so the first admin can get in.
    bootstrap_admins: str = ""
    ldap_timeout_seconds: int = 10

    # Microsoft Graph (Outlook / Microsoft 365)
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    graph_mailbox: str = ""  # e.g. customercare@fnb.example
    poll_interval_seconds: int = 30

    # Encrypts database passwords saved in the dashboard. Generate once and keep it safe:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # In development it falls back to a key derived from the session secret.
    encryption_key: str = ""
    # Phase 2 switch. Off: topics may only give the same answer to everyone (fees, hours, ...).
    # On: topics may also read a customer's own records (balances, transactions), and only for
    # senders verified by Microsoft 365. Turn on only after IT and security sign-off.
    customer_data_answers: bool = False
    # How many bank databases admins may add while customer data answers are off.
    phase1_max_data_sources: int = 1
    # Alerts and digests may only go to addresses in these domains (comma-separated), so the
    # app can never be configured to send customer details outside the bank.
    internal_email_domains: str = ""
    # The dashboard's address, used for links in alert emails, e.g. https://emailcare.fnb.local
    app_base_url: str = ""
    query_timeout_seconds: int = 15
    query_max_rows: int = 50

    # Self-hosted language model (e.g. Qwen3.5) behind an OpenAI-compatible endpoint such as
    # vLLM, Ollama or llama.cpp. Off by default: everything then works exactly as before.
    llm_enabled: bool = False
    llm_base_url: str = ""  # e.g. http://llm:8000/v1 (vLLM) or http://localhost:11434/v1 (Ollama)
    llm_api_key: str = ""  # only if the model server asks for one
    llm_fast_model: str = ""  # routine work: classification
    llm_strong_model: str = ""  # drafting answers; defaults to the fast model
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 1
    llm_max_concurrency: int = 2
    # Extra JSON merged into every request, for server-specific options. For Qwen on vLLM:
    # {"chat_template_kwargs": {"enable_thinking": false}}
    llm_extra_body: str = ""
    # Customer emails go to the model. Unless the bank has approved an outside provider, the
    # model server must be on the bank's own network (private address or internal host name).
    llm_allow_external: bool = False

    # Attachments: scanned by ClamAV (clamd) before anything reads them. Without a scanner
    # they are listed for staff but never opened by the assistant.
    clamd_host: str = ""
    clamd_port: int = 3310
    attachment_max_mb: int = 10

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> Settings:
        if self.auth_mode == "dev" and self.env != "development":
            raise ValueError("FNB_AUTH_MODE=dev is only allowed when FNB_ENV=development")
        if self.llm_enabled:
            if not (self.llm_base_url and self.llm_fast_model):
                raise ValueError("FNB_LLM_ENABLED needs FNB_LLM_BASE_URL and FNB_LLM_FAST_MODEL")
            if not self.llm_allow_external and not is_internal_url(self.llm_base_url):
                raise ValueError(
                    "FNB_LLM_BASE_URL is not on the bank's network; customer emails would leave the bank. "
                    "Set FNB_LLM_ALLOW_EXTERNAL=true only with written approval."
                )
        if self.env != "production":
            return self
        problems: list[str] = []
        if len(self.session_secret) < 32:
            problems.append("FNB_SESSION_SECRET must be at least 32 characters")
        if not self.cookie_secure:
            problems.append("FNB_COOKIE_SECURE must be true")
        if not self.ldap_url.lower().startswith("ldaps://"):
            problems.append("FNB_LDAP_URL must use ldaps:// so passwords are encrypted in transit")
        if not self.ldap_group:
            problems.append("FNB_LDAP_GROUP is required")
        if not self.encryption_key:
            problems.append("FNB_ENCRYPTION_KEY is required")
        if not self.internal_email_domains.strip():
            problems.append("FNB_INTERNAL_EMAIL_DOMAINS is required")
        if self.database_url.startswith("sqlite"):
            problems.append("production must use PostgreSQL")
        if problems:
            raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def bootstrap_admin_usernames(self) -> set[str]:
        return {u.strip().lower() for u in self.bootstrap_admins.split(",") if u.strip()}

    @property
    def internal_domains(self) -> set[str]:
        return {d.strip().lower().lstrip("@") for d in self.internal_email_domains.split(",") if d.strip()}

    @property
    def llm_drafting_model(self) -> str:
        return self.llm_strong_model or self.llm_fast_model

    @property
    def graph_configured(self) -> bool:
        return all((self.graph_tenant_id, self.graph_client_id, self.graph_client_secret, self.graph_mailbox))


def is_internal_url(url: str) -> bool:
    """True for localhost, private/link-local IPs, and single-label or .local/.internal hosts."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith((".local", ".internal", ".lan", ".localdomain"))
    return ip.is_private or ip.is_loopback or ip.is_link_local


@lru_cache
def get_settings() -> Settings:
    return Settings()
