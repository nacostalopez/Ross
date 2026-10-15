"""One battery, run over every registered tool: an account must never see another account's data.

Two accounts share the database. A has a store with nothing in it; B has a store full of orders, ad spend
and creatives. If any tool read across stores, A would see B's figures. It is parametrized over the
registry itself, so a tool added later is covered without anyone remembering to add it here
(test_ai_tools_guards.py makes sure each one can be called this way).

The positive control matters as much as the negative one: B's store must show data through the same
tools, otherwise "A sees nothing" would pass for a tool that returns nothing to anyone.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.ai.tools import DenialReason, StoreNotFound, ToolContext, all_tools, resolve_context, run_tool
from app.models import Store, Subscription, User
from app.security import hash_password
from app.services.weekly_narrative import format_money

NOW = datetime.now(timezone.utc).replace(microsecond=0)

TOOLS = pytest.mark.parametrize("tool", all_tools(), ids=lambda tool: tool.name)


def _seed_rich_store(client, headers, store_id):
    """Twelve days of orders from different customers on two channels, with ad spend and creatives."""
    orders, spend = [], []
    for i in range(12, 0, -1):
        day = NOW - timedelta(days=i)
        orders.append(
            {
                "order_id": f"b-{i}",
                "time": day.isoformat(),
                "gross_amount": 300.0 + i,
                "currency": "USD",
                "customer_email": f"buyer{i}@example.com",
                "attribution_utm_source": "meta" if i % 2 else "google",
            }
        )
        spend.append({"time": day.isoformat(), "platform": "meta", "campaign_id": "cb", "spend": 50.0 + i})
    # Two customers buy again, so repeat orders exist.
    for i in (2, 3):
        orders.append(
            {
                "order_id": f"b-repeat-{i}",
                "time": (NOW - timedelta(days=1)).isoformat(),
                "gross_amount": 120.0 + i,
                "currency": "USD",
                "customer_email": f"buyer{i}@example.com",
                "attribution_utm_source": "google",
            }
        )
    creatives = [
        {
            "time": (NOW - timedelta(days=2)).isoformat(),
            "platform": "meta",
            "campaign_id": "cb",
            "campaign_name": "Bravo",
            "ad_id": f"ad-{n}",
            "ad_name": f"Bravo ad {n}",
            "spend": 20.0 + n,
            "impressions": 1000 * n,
            "clicks": 10 * n,
        }
        for n in (1, 2, 3)
    ]
    for path, rows in (("orders", orders), ("ad-spend", spend), ("creative-performance", creatives)):
        response = client.post(f"/stores/{store_id}/{path}", headers=headers, json=rows)
        assert response.status_code == 201, (path, response.text)


@pytest.fixture
def tenants(client, test_db_session, test_account, test_user, test_store, other_account, other_user):
    """A: test_user with an empty store. B: other_user with a store full of data."""
    store_b = Store(id=uuid4(), account_id=other_account.id, name="Bravo", platform="shopify", currency="USD", timezone="UTC")
    test_db_session.add(store_b)
    test_db_session.commit()

    login = client.post("/auth/login", json={"email": "otheruser@example.com", "password": "otherpassword123"})
    _seed_rich_store(client, {"Authorization": f"Bearer {login.json()['access_token']}"}, store_b.id)

    return {
        "user_a": test_user,
        "store_a": test_store,
        "user_b": other_user,
        "store_b": store_b,
        "account_b": other_account,
    }


def _nothing_in(data):
    """True when a result holds no data: only zeros, nulls, empty lists and text."""
    if data is None or isinstance(data, (str, bool)):
        return True
    if isinstance(data, (int, float)):
        return data == 0
    if isinstance(data, list):
        return all(_nothing_in(item) for item in data)
    if isinstance(data, dict):
        return all(_nothing_in(value) for value in data.values())
    raise AssertionError(f"Unexpected value in a tool result: {data!r}")


def _shows_nothing(tool, result, store):
    assert result.ok is True, (tool.name, result.message)
    if tool.name == "weekly_narrative":
        # The weekly summary always has three figures; with no data they are all zero.
        zero = format_money(0.0, store.currency)
        return result.data["mood"] == "neutral" and result.data["facts"][0]["value"] == zero
    return _nothing_in(result.data)


@TOOLS
def test_an_account_with_an_empty_store_sees_none_of_another_accounts_data(tool, test_db_session, tenants):
    ctx = resolve_context(test_db_session, tenants["user_a"], tenants["store_a"].id, now=NOW)

    result = run_tool(tool.name, ctx)

    assert _shows_nothing(tool, result, tenants["store_a"]), f"{tool.name} showed account A data it doesn't have: {result.data}"


@TOOLS
def test_the_account_with_data_does_see_it_through_the_same_tool(tool, test_db_session, tenants):
    ctx = resolve_context(test_db_session, tenants["user_b"], tenants["store_b"].id, now=NOW)

    result = run_tool(tool.name, ctx)

    assert not _shows_nothing(tool, result, tenants["store_b"]), f"{tool.name} showed nothing for a store full of data"


@TOOLS
def test_a_context_cannot_be_resolved_for_another_accounts_store(tool, test_db_session, tenants):
    with pytest.raises(StoreNotFound):
        resolve_context(test_db_session, tenants["user_a"], tenants["store_b"].id, now=NOW)
    with pytest.raises(StoreNotFound):
        resolve_context(test_db_session, tenants["user_b"], tenants["store_a"].id, now=NOW)


@TOOLS
def test_a_context_built_by_hand_for_another_accounts_store_is_denied(tool, test_db_session, tenants):
    forged = ToolContext(db=test_db_session, user=tenants["user_a"], store=tenants["store_b"], role="owner", now=NOW)

    result = run_tool(tool.name, forged)

    assert result.ok is False
    assert result.denial is DenialReason.FORBIDDEN
    assert result.data is None


@TOOLS
@pytest.mark.parametrize("identity", ["store_id", "account_id", "user_id"])
def test_a_call_cannot_choose_whose_data_it_reads(tool, identity, test_db_session, tenants):
    ctx = resolve_context(test_db_session, tenants["user_a"], tenants["store_a"].id, now=NOW)

    result = run_tool(tool.name, ctx, {identity: str(tenants["store_b"].id)})

    assert result.ok is False
    assert result.denial is DenialReason.INVALID_PARAMS
    assert str(tenants["store_b"].id) not in result.message


@TOOLS
def test_the_plan_gate_applies_to_every_tool_that_declares_one(tool, test_db_session, tenants):
    test_db_session.add(Subscription(account_id=tenants["account_b"].id, plan_id="starter", status="active"))
    test_db_session.commit()
    ctx = resolve_context(test_db_session, tenants["user_b"], tenants["store_b"].id, now=NOW)

    result = run_tool(tool.name, ctx)

    if tool.plan_feature:
        assert result.ok is False
        assert result.denial is DenialReason.PLAN_REQUIRED
        assert result.data is None
    else:
        assert result.ok is True


@TOOLS
def test_a_viewer_can_run_every_read_tool(tool, test_db_session, tenants):
    viewer = User(
        id=uuid4(),
        account_id=tenants["account_b"].id,
        email="viewer-b@example.com",
        hashed_password=hash_password("viewerpassword123"),
        role="viewer",
    )
    test_db_session.add(viewer)
    test_db_session.commit()
    ctx = resolve_context(test_db_session, viewer, tenants["store_b"].id, now=NOW)

    assert run_tool(tool.name, ctx).ok is True
