"""Roles and permissions: the one place that decides who may do what.

Every person who signs in has one role, set by an admin on the Users screen: officer,
supervisor, auditor (read-only, for internal audit and compliance) or admin.
Each role is granted an explicit set of permissions; endpoints check a permission, never a
role name, so changing what a role can do is a one-line edit here.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OFFICER = "officer"  # handles customer emails
    SUPERVISOR = "supervisor"  # also approves drafts that need a supervisor, sees AI quality and analytics
    AUDITOR = "auditor"  # sees everything needed to reconstruct decisions; changes nothing
    ADMIN = "admin"  # also runs the knowledge base, settings, automation policy and users


class Permission(StrEnum):
    VIEW_OVERVIEW = "overview:view"
    VIEW_QUEUE = "queue:view"  # the "Needs attention" list
    HANDLE_QUEUE = "queue:handle"  # assign, mark handled, copy suggested answers
    VIEW_EMAIL_LOG = "emails:view"
    EXPORT_EMAIL_LOG = "emails:export"
    VIEW_KNOWLEDGE = "knowledge:view"
    EDIT_KNOWLEDGE = "knowledge:edit"  # upload documents and new versions, archive
    PUBLISH_KNOWLEDGE = "knowledge:publish"  # put someone else's upload into use
    VIEW_TOPICS = "topics:view"
    EDIT_TOPICS = "topics:edit"  # propose a new topic or a change (incl. its query)
    APPROVE_TOPICS = "topics:approve"  # approve/reject someone else's change, switch on/off
    USE_TEST_AREA = "test:use"  # see which topic an email would match
    RUN_TEST_QUERIES = "test:run-queries"  # also read real data from the bank's databases
    MANAGE_DATA_SOURCES = "datasources:manage"  # add/edit bank database connections
    VIEW_SETTINGS = "settings:view"
    EDIT_SETTINGS = "settings:edit"
    VIEW_USERS = "users:view"
    MANAGE_USERS = "users:manage"  # change roles, switch access off
    VIEW_AUDIT = "audit:view"
    SEND_REPLIES = "replies:send"  # edit an AI draft or write a reply, and send it from the dashboard
    APPROVE_REPLIES = "replies:approve"  # approve and send drafts that need a supervisor
    VIEW_CASES = "cases:view"
    HANDLE_CASES = "cases:handle"  # notes, status, workflow actions
    APPROVE_ACTIONS = "actions:approve"  # approve workflow actions that need a second person
    VIEW_AI = "ai:view"  # model health, quality, evaluation results, policy history
    MANAGE_AUTOMATION = "ai:manage"  # operating mode, kill switch, category policies
    VIEW_ANALYTICS = "analytics:view"
    VIEW_RESTRICTED_KNOWLEDGE = "knowledge:restricted"


_OFFICER = {
    Permission.VIEW_OVERVIEW,
    Permission.VIEW_QUEUE,
    Permission.HANDLE_QUEUE,
    Permission.VIEW_EMAIL_LOG,
    Permission.VIEW_KNOWLEDGE,
    Permission.VIEW_TOPICS,
    Permission.USE_TEST_AREA,
    Permission.SEND_REPLIES,
    Permission.VIEW_CASES,
    Permission.HANDLE_CASES,
}
_SUPERVISOR = _OFFICER | {
    Permission.APPROVE_REPLIES,
    Permission.APPROVE_ACTIONS,
    Permission.EXPORT_EMAIL_LOG,
    Permission.VIEW_AI,
    Permission.VIEW_ANALYTICS,
    Permission.VIEW_RESTRICTED_KNOWLEDGE,
    Permission.VIEW_SETTINGS,
}
_AUDITOR = {
    Permission.VIEW_OVERVIEW,
    Permission.VIEW_QUEUE,
    Permission.VIEW_EMAIL_LOG,
    Permission.EXPORT_EMAIL_LOG,
    Permission.VIEW_KNOWLEDGE,
    Permission.VIEW_RESTRICTED_KNOWLEDGE,
    Permission.VIEW_TOPICS,
    Permission.VIEW_CASES,
    Permission.VIEW_AI,
    Permission.VIEW_ANALYTICS,
    Permission.VIEW_SETTINGS,
    Permission.VIEW_USERS,
    Permission.VIEW_AUDIT,
}
_ADMIN = set(Permission)  # everything

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OFFICER: frozenset(_OFFICER),
    Role.SUPERVISOR: frozenset(_SUPERVISOR),
    Role.AUDITOR: frozenset(_AUDITOR),
    Role.ADMIN: frozenset(_ADMIN),
}


def permissions_for(role: str) -> frozenset[Permission]:
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def has_permission(role: str, permission: Permission) -> bool:
    return permission in permissions_for(role)
