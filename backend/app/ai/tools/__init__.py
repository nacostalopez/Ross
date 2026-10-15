"""The assistant's tools: fixed, typed ways to read (and later change) a store's data.

Importing this package registers every tool. Run one with `run_tool(name, ctx, params)`, where `ctx` comes
from `resolve_context`, so the store and the role always come from the signed-in user.
"""

from app.ai.tools import analytics  # noqa: F401  (registers the analytics tools)
from app.ai.tools.base import DenialReason, Risk, Tool, ToolContext, ToolResult
from app.ai.tools.context import StoreNotFound, resolve_context
from app.ai.tools.policy import Decision, authorize
from app.ai.tools.registry import all_tools, available_tools, get_tool, run_tool

__all__ = [
    "Decision",
    "DenialReason",
    "Risk",
    "StoreNotFound",
    "Tool",
    "ToolContext",
    "ToolResult",
    "all_tools",
    "authorize",
    "available_tools",
    "get_tool",
    "resolve_context",
    "run_tool",
]
