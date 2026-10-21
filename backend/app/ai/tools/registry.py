"""The registered tools, and the one way to run them."""

import logging
import time
from typing import Optional

from pydantic import TypeAdapter, ValidationError

from app.ai import traces
from app.ai.tools.base import DenialReason, Tool, ToolContext, ToolResult
from app.ai.tools.policy import authorize

logger = logging.getLogger("ross.assistant")

_TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _TOOLS:
        raise ValueError(f"Tool {tool.name!r} is already registered")
    _TOOLS[tool.name] = tool
    return tool


def all_tools() -> list[Tool]:
    return sorted(_TOOLS.values(), key=lambda t: t.name)


def get_tool(name: str) -> Optional[Tool]:
    return _TOOLS.get(name)


def available_tools(ctx: ToolContext) -> list[Tool]:
    """The tools this user could run here right now, which is what the router or a model may be offered."""
    return [tool for tool in all_tools() if authorize(ctx, tool).allowed]


def _denied(name: str, reason: DenialReason, message: str) -> ToolResult:
    return ToolResult(ok=False, tool=name, denial=reason, message=message)


def _describe_errors(error: ValidationError) -> str:
    # Field and rule only: never the value that was sent.
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'parámetros'}: {e['msg']}" for e in error.errors())


def run_tool(name: str, ctx: ToolContext, raw_params: Optional[dict] = None) -> ToolResult:
    """Run one tool for the user in `ctx`. Never raises for something the caller got wrong: it answers
    with a typed denial the assistant can explain. Every call, answered or denied, leaves one trace here
    and nowhere else; recording is observation and never changes the answer."""
    started = time.perf_counter()
    result, validated = _execute(name, ctx, raw_params)
    try:
        traces.record_trace(
            ctx.db,
            user=ctx.user,
            store_id=ctx.store.id,
            tool=name,
            outcome="ok" if result.ok else result.denial.value,
            latency_ms=round((time.perf_counter() - started) * 1000),
            role=ctx.role,
            params=validated,
        )
    except Exception:
        logger.warning("trace_not_recorded", extra={"tool": name}, exc_info=True)
    return result


def _execute(name: str, ctx: ToolContext, raw_params: Optional[dict]) -> tuple[ToolResult, Optional[dict]]:
    """The call itself. Also returns the validated parameters, or None when there are none to trust."""
    tool = _TOOLS.get(name)
    if tool is None:
        return _denied(name, DenialReason.UNKNOWN_TOOL, "No existe esa herramienta"), None

    # Authorization first, so a call that isn't allowed learns nothing from how its parameters are checked.
    decision = authorize(ctx, tool)
    if not decision.allowed:
        return _denied(name, decision.reason, decision.message), None

    try:
        params = tool.params.model_validate(raw_params or {})
    except ValidationError as error:
        return _denied(name, DenialReason.INVALID_PARAMS, _describe_errors(error)), None

    output = tool.run(ctx, params)

    adapter = TypeAdapter(tool.result)
    data = adapter.dump_python(adapter.validate_python(output.data), mode="json")
    window = None
    if output.window:
        window = {"start": output.window[0].isoformat(), "end": output.window[1].isoformat()}
    validated = params.model_dump()
    result = ToolResult(
        ok=True,
        tool=name,
        data=data,
        params=validated,
        as_of=ctx.now,
        window=window,
        currency=ctx.store.currency,
    )
    return result, validated
