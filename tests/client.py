"""Authenticated test client; security tests also use the raw client."""

from fastapi.testclient import TestClient


class LocalClient(TestClient):
    def __init__(self, app, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = "Bearer " + app.state.local_access.token
        super().__init__(app, headers=headers, **kwargs)
