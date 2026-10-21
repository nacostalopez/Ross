"""What a trace may hold, checked over the whole registry (docs/ia/fase-4-2-diseno.md, steps 4 and 8).

Like test_ai_tools_guards.py, it walks the registry: a tool added later is run and its trace inspected
without anyone remembering to add it here.
"""
import pytest

from app.ai import traces
from app.ai.tools import DenialReason, all_tools, resolve_context, run_tool
from app.models import AssistantTrace

# The only columns a trace has. A free-text or value column added later fails here and has to be argued for.
ALLOWED_COLUMNS = {
    "id", "account_id", "store_id", "tool", "tier", "provider", "outcome", "validator_rejected",
    "param_shape", "latency_ms", "role", "created_at",
}

# What a hostile or careless caller might send: values that must never reach a trace.
SECRET = "ventas de Ana Perez ana@example.com 1234567"
CALLS = ({}, {"days": 14}, {"days": SECRET}, {"note": SECRET}, {"store_id": SECRET})

TOOLS = pytest.mark.parametrize("tool", all_tools(), ids=lambda tool: tool.name)


def test_a_trace_has_no_column_for_free_text_or_figures():
    assert {column.name for column in AssistantTrace.__table__.columns} == ALLOWED_COLUMNS


@pytest.mark.db
@TOOLS
def test_no_trace_keeps_a_value_outside_the_bounded_list(tool, test_db_session, test_user, test_store):
    ctx = resolve_context(test_db_session, test_user, test_store.id)
    for params in CALLS:
        run_tool(tool.name, ctx, params)

    rows = test_db_session.query(AssistantTrace).filter_by(tool=tool.name).all()
    assert len(rows) == len(CALLS)
    valid_outcomes = {"ok"} | {reason.value for reason in DenialReason}
    for row in rows:
        assert set(row.param_shape) <= traces.BOUNDED_PARAMS
        assert all(isinstance(v, int) and not isinstance(v, bool) for v in row.param_shape.values())
        assert row.outcome in valid_outcomes
        everything = " ".join(str(getattr(row, name)) for name in ALLOWED_COLUMNS)
        for leaked in ("Ana Perez", "ana@example.com", "1234567", test_user.email, test_store.name):
            assert leaked not in everything


@pytest.mark.db
@TOOLS
def test_one_accounts_traces_are_invisible_to_another(tool, test_db_session, test_user, test_store, other_account):
    ctx = resolve_context(test_db_session, test_user, test_store.id)
    run_tool(tool.name, ctx, {})

    assert test_db_session.query(AssistantTrace).filter_by(account_id=other_account.id).count() == 0
    theirs = traces.utility_score(test_db_session, account_id=other_account.id, tool=tool.name)
    assert theirs.runs == 0 and theirs.validator_rejection_rate is None
    mine = traces.utility_score(test_db_session, account_id=test_user.account_id, tool=tool.name)
    assert mine.runs == 1
