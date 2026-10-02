"""POST /demo-requests — the landing's demo form."""

import pytest
from fastapi import status

from app.models import DemoRequest
from app.routes import demo_requests as demo_requests_route

VALID = {"name": "Ana Pérez", "email": "ana@tienda.com", "platform": "tiendanube"}


@pytest.mark.db
class TestCreateDemoRequest:
    def test_stores_the_request_and_notifies_the_team(self, client, test_db_session, monkeypatch):
        sent = []
        monkeypatch.setattr(demo_requests_route, "send_email", lambda **kw: sent.append(kw))

        response = client.post("/demo-requests", json=VALID)

        assert response.status_code == status.HTTP_201_CREATED
        assert response.json() == {"status": "received"}
        row = test_db_session.query(DemoRequest).one()
        assert (row.name, row.email, row.platform) == ("Ana Pérez", "ana@tienda.com", "tiendanube")
        assert len(sent) == 1
        assert sent[0]["to"] == "ross@aramal.co"
        assert "ana@tienda.com" in sent[0]["body"]
        assert "Tiendanube" in sent[0]["body"]

    def test_email_failure_still_keeps_the_request(self, client, test_db_session, monkeypatch):
        def boom(**kw):
            raise OSError("SMTP down")

        monkeypatch.setattr(demo_requests_route, "send_email", boom)

        response = client.post("/demo-requests", json=VALID)

        assert response.status_code == status.HTTP_201_CREATED
        assert test_db_session.query(DemoRequest).count() == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {**VALID, "email": "not-an-email"},
            {**VALID, "platform": "amazon"},
            {**VALID, "name": ""},
            {"email": "ana@tienda.com", "platform": "shopify"},
        ],
    )
    def test_invalid_payload_is_rejected(self, client, test_db_session, payload):
        response = client.post("/demo-requests", json=payload)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert test_db_session.query(DemoRequest).count() == 0

    def test_rate_limited_per_ip(self, client, monkeypatch):
        monkeypatch.setattr(demo_requests_route, "send_email", lambda **kw: None)

        codes = [client.post("/demo-requests", json=VALID).status_code for _ in range(6)]

        assert codes[:5] == [status.HTTP_201_CREATED] * 5
        assert codes[5] == status.HTTP_429_TOO_MANY_REQUESTS
