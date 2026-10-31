"""Pure presentation helpers (no Streamlit import, so they are easy to test)."""

from __future__ import annotations

from typing import Any

from ase.core.models import Plan, RepositoryInventory, RunRecord
from ase.core.states import RunState

STATE_LABELS: dict[RunState, str] = {
    RunState.CREATED: "Created",
    RunState.IMPORTED: "Imported",
    RunState.ANALYZED: "Analyzed",
    RunState.PLAN_READY: "Plan ready",
    RunState.AWAITING_APPROVAL: "Awaiting approval",
    RunState.EXECUTING: "Executing",
    RunState.BUILT: "Built (not verified)",
    RunState.VERIFYING: "Verifying",
    RunState.REPAIRING: "Repairing",
    RunState.COMPLETED: "Completed",
    RunState.FAILED: "Failed",
    RunState.CANCELLED: "Cancelled",
    RunState.BLOCKED: "Blocked",
}

NO_PROVIDER_BANNER = (
    "No AI provider is configured, so ASE can import and inspect repositories but cannot "
    "audit them. Set the ASE_LLM_* values in .env (see .env.example)."
)


def state_label(state: RunState) -> str:
    return STATE_LABELS[state]


def short_id(run_id: str) -> str:
    return run_id[:8]


def run_row(run: RunRecord) -> dict[str, Any]:
    return {
        "Run": short_id(run.run_id),
        "State": state_label(run.state),
        "Source": run.source or "-",
        "Created (UTC)": run.created_at.strftime("%Y-%m-%d %H:%M"),
        "Limitations": len(run.limitations),
    }


def history_rows(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {
            "At (UTC)": c.at.strftime("%H:%M:%S"),
            "From": state_label(c.from_state),
            "To": state_label(c.to_state),
            "Reason": c.reason or "-",
        }
        for c in run.history
    ]


def action_rows(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {"Action": a.name, "Result": a.exit_status, "Summary": a.summary or "-"}
        for a in run.actions
    ]


def format_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # unreachable; keeps type checkers happy


def inventory_rows(inv: RepositoryInventory, limit: int = 500) -> list[dict[str, Any]]:
    return [
        {
            "Path": e.path,
            "Size": format_bytes(e.size_bytes),
            "Language": e.language or "-",
            "Excluded from AI context": e.excluded.value if e.excluded else "no",
        }
        for e in inv.files[:limit]
    ]


def change_rows(plan: Plan) -> list[dict[str, Any]]:
    return [
        {
            "Change": c.id,
            "Action": c.action,
            "File": c.path,
            "Risk": c.risk,
            "Fixes": ", ".join(c.finding_ids),
            "What": c.summary,
            "How it will be verified": c.verification or "-",
        }
        for c in plan.changes
    ]
