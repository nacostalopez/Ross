"""Tests for the per-user customizable dashboard layout."""

import pytest
from fastapi import status

from app.models import DashboardLayout
from app.schemas.dashboard import DEFAULT_WIDGETS


@pytest.mark.db
class TestGetLayout:
    def test_no_saved_layout_returns_the_default(self, client, auth_header):
        response = client.get("/dashboard/layout", headers=auth_header)
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["widgets"] == DEFAULT_WIDGETS

    def test_requires_auth(self, client):
        response = client.get("/dashboard/layout")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.db
class TestSetLayout:
    def test_saves_a_custom_layout_and_get_returns_it(self, client, auth_header):
        custom = [{"type": "stat_roas", "hero": True}, {"type": "chart_daily", "hero": False}]
        put_response = client.put("/dashboard/layout", headers=auth_header, json={"widgets": custom})
        assert put_response.status_code == status.HTTP_200_OK
        assert put_response.json()["widgets"] == custom

        get_response = client.get("/dashboard/layout", headers=auth_header)
        assert get_response.json()["widgets"] == custom

    def test_second_save_overwrites_the_first_not_append(self, client, auth_header, test_db_session, test_user):
        client.put(
            "/dashboard/layout",
            headers=auth_header,
            json={"widgets": [{"type": "stat_roas", "hero": True}]},
        )
        client.put(
            "/dashboard/layout",
            headers=auth_header,
            json={"widgets": [{"type": "chart_daily", "hero": False}]},
        )

        rows = test_db_session.query(DashboardLayout).filter_by(user_id=test_user.id).all()
        assert len(rows) == 1
        assert rows[0].widgets == [{"type": "chart_daily", "hero": False}]

    def test_duplicate_widget_type_rejected(self, client, auth_header):
        response = client.put(
            "/dashboard/layout",
            headers=auth_header,
            json={"widgets": [{"type": "stat_roas"}, {"type": "stat_roas"}]},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_unknown_widget_type_rejected(self, client, auth_header):
        response = client.put(
            "/dashboard/layout",
            headers=auth_header,
            json={"widgets": [{"type": "not_a_real_widget"}]},
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_empty_layout_is_allowed(self, client, auth_header):
        response = client.put("/dashboard/layout", headers=auth_header, json={"widgets": []})
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["widgets"] == []

    def test_viewer_can_customize_their_own_dashboard(self, client, viewer_auth_header):
        """Layout is a personal display preference, not a data-access
        permission — every role may set it, including viewer."""
        response = client.put(
            "/dashboard/layout",
            headers=viewer_auth_header,
            json={"widgets": [{"type": "stat_revenue"}]},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_layout_is_per_user_not_shared_across_the_account(
        self,
        client,
        auth_header,
        admin_auth_header,
    ):
        client.put(
            "/dashboard/layout",
            headers=auth_header,
            json={"widgets": [{"type": "stat_roas", "hero": True}]},
        )

        admin_response = client.get("/dashboard/layout", headers=admin_auth_header)
        assert admin_response.json()["widgets"] == DEFAULT_WIDGETS
