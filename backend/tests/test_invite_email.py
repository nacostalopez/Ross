"""The invite and reminder emails (app/routes/accounts.py::_send_invite_email)."""

import pytest
from fastapi import status

from app.models import AccountInvite
from app.routes import accounts as accounts_route
from app.security import hash_token


@pytest.fixture
def sent(monkeypatch):
    outbox = []
    monkeypatch.setattr(accounts_route, "send_email", lambda **kw: outbox.append(kw))
    return outbox


def _token_in(body):
    link = next(word for word in body.split() if "invite_token=" in word)
    return link.split("invite_token=", 1)[1]


@pytest.mark.db
class TestInviteEmail:
    def test_invite_is_in_spanish_with_role_and_a_working_link(self, client, auth_header, test_account, test_db_session, sent):
        response = client.post("/accounts/invites", headers=auth_header, json={"email": "nueva@example.com", "role": "admin"})

        assert response.status_code == status.HTTP_201_CREATED
        assert len(sent) == 1
        email = sent[0]
        assert email["to"] == "nueva@example.com"
        assert email["subject"] == f"Te invitaron a {test_account.name} en ROSS"
        assert "te invitó a sumarte" in email["body"]
        assert "como Admin" in email["body"]
        assert "vence en 7 días" in email["body"]
        invite = test_db_session.query(AccountInvite).filter_by(email="nueva@example.com").one()
        assert invite.token_hash == hash_token(_token_in(email["body"]))

    def test_reminder_says_so_and_carries_the_new_token(self, client, auth_header, test_account, test_db_session, sent):
        invite_id = client.post(
            "/accounts/invites", headers=auth_header, json={"email": "nueva@example.com", "role": "viewer"}
        ).json()["id"]

        response = client.post(f"/accounts/invites/{invite_id}/resend", headers=auth_header)

        assert response.status_code == status.HTTP_200_OK
        reminder = sent[-1]
        assert reminder["subject"] == f"Recordatorio: te invitaron a {test_account.name} en ROSS"
        assert "como Viewer" in reminder["body"]
        invite = test_db_session.query(AccountInvite).filter_by(email="nueva@example.com").one()
        assert invite.token_hash == hash_token(_token_in(reminder["body"]))
