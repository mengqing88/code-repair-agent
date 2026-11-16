"""Existing API regressions authenticate as an explicitly migrated fixture owner."""
from fastapi.testclient import TestClient as AnonymousClient
from fastapi import HTTPException
from code_repair.user_accounts import SESSION_COOKIE, csrf_token


class TestClient(AnonymousClient):
    __test__ = False

    def __init__(self, app, *args, **kwargs):
        super().__init__(app, *args, **kwargs)
        accounts = app.state.runner.accounts
        try:
            user = accounts.login("fixture_owner", "fixture-password-123")
        except HTTPException:
            user = accounts.register("fixture_owner", "fixture-password-123")
        accounts.bind_legacy(user["id"])
        token = accounts.new_session(user["id"])
        self.cookies.set(SESSION_COOKIE, token)
        self.headers.update({"X-CSRF-Token": csrf_token(token), "X-Account-ID": user["id"]})
