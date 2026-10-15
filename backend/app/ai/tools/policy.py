"""The single place that decides whether a tool may run.

Role, plan, module and risk are checked here and nowhere else. The decision never reads a confidence
score: however sure the assistant is about what the user meant, that doesn't authorize anything.
"""

from dataclasses import dataclass
from typing import Optional

from app.ai.tools.base import DenialReason, Risk, Tool, ToolContext
from app.dependencies import check_plan_feature, is_demo_user

ROLE_RANK = {"viewer": 0, "admin": 1, "owner": 2}

# Modules every account has. The CRM joins this set per account once it exists (stage 4.4).
ALWAYS_ACTIVE_MODULES = frozenset({"analytics"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: Optional[DenialReason] = None
    message: Optional[str] = None


ALLOW = Decision(True)


def _deny(reason: DenialReason, message: str) -> Decision:
    return Decision(False, reason, message)


def module_active(ctx: ToolContext, module: str) -> bool:
    return module in ALWAYS_ACTIVE_MODULES


def authorize(ctx: ToolContext, tool: Tool) -> Decision:
    if not module_active(ctx, tool.module):
        return _deny(DenialReason.MODULE_INACTIVE, "Esta función no está activada en tu cuenta")

    if ROLE_RANK.get(ctx.role, -1) < ROLE_RANK[tool.min_role]:
        return _deny(DenialReason.FORBIDDEN, "No tenés permiso para hacer esto en esta tienda")

    if tool.plan_feature:
        allowed, plan = check_plan_feature(ctx.db, ctx.user.account_id, tool.plan_feature)
        if not allowed:
            return _deny(DenialReason.PLAN_REQUIRED, f"Esta funcionalidad requiere un plan superior a {plan.name}")

    if tool.risk is Risk.HIGH:
        return _deny(DenialReason.HIGH_RISK_DISABLED, "Esta acción todavía no está habilitada")

    if tool.risk is Risk.WRITE_LOW:
        if is_demo_user(ctx.user):
            return _deny(DenialReason.DEMO_READ_ONLY, "La cuenta de demostración es de solo lectura")
        if not ctx.confirmed:
            return _deny(DenialReason.CONFIRMATION_REQUIRED, "Hace falta tu confirmación antes de hacer este cambio")

    return ALLOW
