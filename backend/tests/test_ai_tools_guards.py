"""Every registered tool, checked from the registry itself rather than tool by tool.

A tool that forgets its role, takes an identity as a parameter, can be run without the policy, or
isn't covered by the isolation battery would reach the assistant unnoticed. These tests walk the
registry and fail on any of them, so the mistake is caught when the tool is added. Same idea as
test_route_guards.py, for the assistant instead of the API.
"""
import re
from pathlib import Path

from pydantic import TypeAdapter

from app.ai.tools import all_tools
from app.ai.tools.base import IDENTITY_PARAMS, Risk
from app.ai.tools.policy import ROLE_RANK

APP = Path(__file__).resolve().parent.parent / "app"

# Tools that change something. The generic isolation battery only reads, so each one of these needs its own
# tests for confirmation and audit, and goes here once it has them. Empty while every tool is read-only.
WRITE_TOOLS_WITH_THEIR_OWN_TESTS: set[str] = set()


def test_the_walk_finds_the_whole_registry():
    # If tools stopped registering, the checks below would pass by checking nothing.
    assert len(all_tools()) >= 9


def test_every_tool_declares_what_the_policy_needs():
    problems = []
    for tool in all_tools():
        if not tool.description.strip():
            problems.append(f"{tool.name}: no description")
        if not tool.module:
            problems.append(f"{tool.name}: no module")
        if tool.min_role not in ROLE_RANK:
            problems.append(f"{tool.name}: unknown min_role {tool.min_role!r}")
        if not isinstance(tool.risk, Risk):
            problems.append(f"{tool.name}: risk is not a Risk")
        if tool.result is None:
            problems.append(f"{tool.name}: no output type")
        else:
            try:
                TypeAdapter(tool.result)
            except Exception as error:  # noqa: BLE001 - any failure to build the adapter is the finding
                problems.append(f"{tool.name}: the output type can't be validated ({error})")
    assert not problems, problems


def test_no_tool_takes_an_identity_as_a_parameter():
    leaky = {tool.name: sorted(IDENTITY_PARAMS & set(tool.params.model_fields)) for tool in all_tools()}
    assert not {name: fields for name, fields in leaky.items() if fields}


def test_every_tool_can_be_called_with_no_parameters_so_the_battery_can_cover_it():
    # The isolation battery calls each tool with no parameters. A tool with a required one needs the
    # battery taught how to call it, and until then it must not reach the registry unnoticed.
    uncallable = []
    for tool in all_tools():
        try:
            tool.params.model_validate({})
        except Exception:  # noqa: BLE001
            uncallable.append(tool.name)
    assert not uncallable, f"Tools the isolation battery can't call without parameters: {uncallable}"


def test_every_tool_that_changes_something_has_its_own_tests():
    undeclared = sorted(
        tool.name for tool in all_tools() if tool.risk is not Risk.READ and tool.name not in WRITE_TOOLS_WITH_THEIR_OWN_TESTS
    )
    assert not undeclared, f"Tools that aren't read-only and have no confirmation and audit tests: {undeclared}"


def _python_files_under_app():
    return [path for path in APP.rglob("*.py") if "__pycache__" not in path.parts]


def test_a_tool_is_only_ever_run_through_the_registry():
    runs_a_tool = re.compile(r"\btool\.run\(")
    found = sorted(
        str(path.relative_to(APP))
        for path in _python_files_under_app()
        if runs_a_tool.search(path.read_text(encoding="utf-8"))
    )
    assert found == [str(Path("ai") / "tools" / "registry.py")], f"Tools run outside the registry (and its policy): {found}"


def test_only_the_policy_decides_plan_and_demo_access_for_tools():
    deciders = re.compile(r"\b(check_plan_feature|is_demo_user)\b")
    outside_the_policy = sorted(
        str(path.relative_to(APP))
        for path in (APP / "ai").rglob("*.py")
        # traces.py only decides whether to *record* a call for the demo user; it grants no access.
        if path.name not in ("policy.py", "traces.py") and deciders.search(path.read_text(encoding="utf-8"))
    )
    assert not outside_the_policy, f"Plan or demo checks outside the policy: {outside_the_policy}"
