"""Streamlit rendering. Talks only to ProjectService and presenters.

Rule: the UI shows only what is recorded on a run or measured from the environment.
It never shows invented progress, invented results, or buttons that pretend to act.
"""

from __future__ import annotations

import streamlit as st

from ase.core.errors import AseError
from ase.core.models import (
    AuditReport,
    BuildReport,
    Plan,
    RepositoryInventory,
    RunRecord,
    VerifyReport,
)
from ase.core.states import RunState, is_terminal
from ase.services.project_service import EnvironmentStatus, ProjectService
from ase.ui.presenters import (
    NO_PROVIDER_BANNER,
    action_rows,
    change_rows,
    format_bytes,
    history_rows,
    inventory_rows,
    run_row,
    short_id,
    state_label,
    usage_rows,
)

TAB_TITLES = [
    "Dashboard",
    "1. Analyze & Import",
    "2. Audit",
    "3. Architecture & Plan",
    "4. Build",
    "5. Test & Verify",
    "6. Push & Deploy",
]

# (tab index, what the user should know right now)
NOT_BUILT: dict[int, str] = {
    6: "Not built yet. Push and deploy are disabled: nothing can be sent to GitHub or a host.",
}

_STAGE_LABELS = {
    "import": "Step 1: import and analyze",
    "audit": "Step 2: audit",
    "plan": "Step 3: architecture and plan",
    "build": "Step 4: build",
    "verify": "Step 5: test and verify",
}
_STATUS_ICONS = {"running": "...", "done": "done", "failed": "FAILED", "skipped": "skipped"}


def render_app(service: ProjectService) -> None:
    env = service.environment_status()
    st.title("ASE - Autonomous Software Engineer")
    if not env.llm_configured:
        st.warning(NO_PROVIDER_BANNER)
    _sidebar(env)

    tabs = st.tabs(TAB_TITLES)
    with tabs[0]:
        _dashboard(service)
    with tabs[1]:
        _analyze_tab(service)
    with tabs[2]:
        _audit_tab(service)
    with tabs[3]:
        _plan_tab(service)
    with tabs[4]:
        _build_tab(service)
    with tabs[5]:
        _verify_tab(service)
    for index, message in NOT_BUILT.items():
        with tabs[index]:
            st.subheader(TAB_TITLES[index])
            st.info(message)


def _selected_run(service: ProjectService, *, needs: str) -> RunRecord | None:
    """The run chosen on the dashboard (or the newest one that has the data this tab needs)."""
    runs = [r for r in service.list_runs() if getattr(r, needs) is not None]
    if not runs:
        return None
    selected = st.session_state.get("selected_run")
    return next((r for r in runs if r.run_id == selected), runs[0])


def _sidebar(env: EnvironmentStatus) -> None:
    with st.sidebar:
        st.subheader("Environment")
        if env.routes:
            st.write("AI model per step:")
            for route in env.routes:
                st.write(f"- {route.stage}: **{route.model or '?'}** via {route.provider}")
            st.caption("Taken from settings. Connectivity is checked when a step runs.")
        if not env.llm_configured:
            st.write("AI model: **not fully configured**")
        st.write("GitHub token: " + ("configured" if env.github_token_configured else "not set"))
        execution = "available" if env.code_execution_available else "unavailable"
        st.write(f"Code execution: {execution}")
        st.caption(env.code_execution_note)
        if not env.git_revocation_check_enabled:
            st.warning("Git certificate revocation check is OFF (ASE_GIT_CHECK_CERT_REVOCATION).")


def _dashboard(service: ProjectService) -> None:
    left, right = st.columns(2)
    with left:
        st.subheader("Import a GitHub repository")
        with st.form("pipeline_form", clear_on_submit=False):
            url = st.text_input("Repository URL", placeholder="https://github.com/owner/repo")
            submitted = st.form_submit_button("Run steps 1-5 automatically")
        st.caption("Public github.com repositories only. Code is read, never executed.")
        if submitted:
            _run_pipeline(service, url.strip())
    with right:
        st.subheader("Create a new project from a prompt")
        st.text_area("Describe the project", disabled=True, height=100)
        st.button("Create project", disabled=True)
        st.caption("Not available yet. Planned for the plan and build steps.")

    st.divider()
    _runs_section(service)


def _run_pipeline(service: ProjectService, url: str) -> None:
    def on_stage(stage: str, status: str) -> None:
        label = _STAGE_LABELS.get(stage, stage)
        st.write(f"{label}: {_STATUS_ICONS.get(status, status)}")

    try:
        with st.status("Working...", expanded=True) as box:
            run = service.run_pipeline(url, on_stage)
            if run.state is RunState.FAILED or (
                run.actions and run.actions[-1].exit_status == "error"
            ):
                box.update(label="Stopped: a step failed", state="error")
            else:
                box.update(label="Finished", state="complete")
    except AseError as exc:
        st.error(f"Rejected: {exc.message}")
        return

    st.session_state["selected_run"] = run.run_id
    last = run.actions[-1] if run.actions else None
    if run.state is RunState.FAILED:
        reason = run.history[-1].reason if run.history else "unknown error"
        step = last.name if last is not None else "pipeline"
        st.error(f"Stopped at {step}: {reason}")
    elif last is not None and last.exit_status == "error":
        st.error(f"Step failed ({last.name}): {last.summary}")
    elif run.state is RunState.COMPLETED and run.final_status == "completed_verified":
        st.success(
            f"Run {short_id(run.run_id)}: built AND verified. The tests passed in the sandbox."
        )
    elif run.state is RunState.COMPLETED:
        st.warning(
            f"Run {short_id(run.run_id)}: built, but NOT fully verified. See 5. Test & Verify."
        )
    elif run.state is RunState.BUILT:
        st.warning(
            f"Run {short_id(run.run_id)}: the code is built but NOT verified (tests did not run)."
        )
    elif run.state is RunState.ANALYZED:
        st.info(f"Run {short_id(run.run_id)}: audit completed; there was nothing to plan.")
    else:
        st.warning(f"Run {short_id(run.run_id)}: stopped before the build step.")


def _runs_section(service: ProjectService) -> None:
    st.subheader("Recent runs")
    runs = service.list_runs()
    if not runs:
        st.info("No runs yet. Import a repository above to create the first one.")
        return

    st.dataframe([run_row(r) for r in runs], hide_index=True, width="stretch")
    ids = [r.run_id for r in runs]
    default = st.session_state.get("selected_run")
    index = ids.index(default) if default in ids else 0
    chosen = st.selectbox("Inspect run", ids, index=index, format_func=lambda rid: short_id(rid))
    run = next(r for r in runs if r.run_id == chosen)
    st.session_state["selected_run"] = chosen

    st.markdown(f"**State:** {state_label(run.state)}  |  **Source:** {run.source or '-'}")
    usage = usage_rows(run)
    if usage:
        st.markdown("**AI models used in this run** (from the recorded reports)")
        st.dataframe(usage, hide_index=True, width="stretch")
    for item in run.limitations:
        st.warning(item)
    if run.history:
        st.caption("State history")
        st.dataframe(history_rows(run), hide_index=True, width="stretch")
    if run.actions:
        st.caption("Actions")
        st.dataframe(action_rows(run), hide_index=True, width="stretch")
    if not is_terminal(run.state) and st.button("Cancel this run", key=f"cancel_{run.run_id}"):
        try:
            service.cancel_run(run.run_id)
        except AseError as exc:
            st.error(exc.message)
        else:
            st.rerun()


def _analyze_tab(service: ProjectService) -> None:
    st.subheader("Step 1: Analyze & Import")
    run = _selected_run(service, needs="inventory")
    if run is None or run.inventory is None:
        st.info("No repository has been imported yet. Use the Dashboard.")
        return
    inv: RepositoryInventory = run.inventory
    st.caption(f"Run {short_id(run.run_id)} | {run.source}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files", inv.total_files)
    c2.metric("Size", format_bytes(inv.total_bytes))
    c3.metric("Main language", inv.primary_language or "unknown")
    c4.metric("Ecosystems", ", ".join(inv.ecosystems) or "unknown")
    for note in inv.notes:
        st.warning(note)

    left, right = st.columns(2)
    with left:
        st.markdown("**Dependency files**")
        st.write(inv.dependency_files or "none found")
        st.markdown("**Entry points (by filename)**")
        st.write(inv.entry_points or "none found")
    with right:
        st.markdown("**Test files**")
        st.write(inv.test_files or "none found")
        st.markdown("**Build / deploy / CI files**")
        st.write(inv.devops_files or "none found")
    st.markdown("**Framework hints**")
    st.write(inv.framework_hints or "none")
    st.caption("Hints come from words in dependency files and are not verified.")

    if inv.excluded_counts:
        pretty = ", ".join(f"{k}: {v}" for k, v in sorted(inv.excluded_counts.items()))
        st.info(f"Excluded from AI context: {pretty}")
    if inv.skipped_directories:
        st.caption("Skipped directories: " + ", ".join(inv.skipped_directories))
    st.dataframe(inventory_rows(inv), hide_index=True, width="stretch")
    if len(inv.files) > 500:
        st.caption(f"Showing the first 500 of {len(inv.files)} entries.")


_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _audit_tab(service: ProjectService) -> None:
    st.subheader("Step 2: Audit")
    run = _selected_run(service, needs="inventory")
    if run is None:
        st.info("Import a repository first (Dashboard).")
        return
    st.caption(f"Run {short_id(run.run_id)} | {run.source} | {state_label(run.state)}")

    if run.audit is not None:
        _render_report(run.audit)
        return
    if not service.environment_status().llm_configured:
        st.warning(NO_PROVIDER_BANNER)
        return
    if run.state is RunState.IMPORTED:
        last = run.actions[-1] if run.actions else None
        if last is not None and last.name == "ai_audit" and last.exit_status == "error":
            st.error(f"The last audit attempt failed: {last.summary}")
        if st.button("Run audit", key=f"audit_{run.run_id}"):
            try:
                with st.spinner("Auditing..."):
                    service.run_audit(run.run_id)
            except AseError as exc:
                st.error(f"Audit failed: {exc.message}")
            else:
                st.rerun()
    else:
        st.info("This run cannot be audited in its current state.")


def _render_report(report: AuditReport) -> None:
    st.write(report.summary or "(no summary)")
    fixes = sum(1 for f in report.findings if f.kind == "fix")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fixes", fixes)
    c2.metric("Additions", len(report.findings) - fixes)
    c3.metric("Discarded (unverifiable)", report.rejected_findings)
    c4.metric("Model calls", report.model_calls)
    for note in report.limitations:
        st.caption(note)
    if report.rejection_reasons:
        st.caption("Discarded because: " + "; ".join(report.rejection_reasons))

    for f in sorted(report.findings, key=lambda x: _SEVERITY_ORDER[x.severity.value]):
        title = f"{f.id} | {f.severity.value.upper()} | {f.kind} | {f.category}"
        with st.expander(title):
            st.markdown(
                f"**AI interpretation** (status: {f.status.value}, confidence {f.confidence:.0%})"
            )
            st.write(f.description)
            for ev in f.evidence:
                loc = f"{ev.file_path}:{ev.line_start}-{ev.line_end}"
                verified = " (location verified)" if ev.location_verified else ""
                st.markdown(f"Evidence `{loc}`{verified}")
                if ev.snippet:
                    st.code(ev.snippet, language=None)
            if f.recommended_fix:
                st.markdown("**Suggested change (not applied)**")
                st.write(f.recommended_fix)
    with st.expander(f"Files sent to the model ({len(report.files_sent)})"):
        st.write(report.files_sent or "none")
    tokens_in = report.prompt_tokens if report.prompt_tokens is not None else "unknown"
    tokens_out = report.completion_tokens if report.completion_tokens is not None else "unknown"
    cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
    st.caption(
        f"Provider: {report.provider} / {report.model} | prompt {report.prompt_version} | "
        f"tokens: {tokens_in} in, {tokens_out} out | cost: {cost}"
    )


def _plan_tab(service: ProjectService) -> None:
    st.subheader("Step 3: Architecture & Plan")
    run = _selected_run(service, needs="inventory")
    if run is None:
        st.info("Import a repository first (Dashboard).")
        return
    st.caption(f"Run {short_id(run.run_id)} | {run.source} | {state_label(run.state)}")

    if run.plan is not None:
        _render_plan(run.plan)
        return
    if run.audit is None:
        st.info("The audit (step 2) must finish first.")
        return
    if not run.audit.findings:
        st.info("The audit found nothing to change, so there is nothing to plan.")
        return
    if not service.environment_status().llm_configured:
        st.warning(NO_PROVIDER_BANNER)
        return
    last = run.actions[-1] if run.actions else None
    if last is not None and last.name == "architecture_plan" and last.exit_status == "error":
        st.error(f"The last planning attempt failed: {last.summary}")
    if run.state is RunState.ANALYZED and st.button("Generate plan", key=f"plan_{run.run_id}"):
        try:
            with st.spinner("Planning..."):
                service.run_plan(run.run_id)
        except AseError as exc:
            st.error(f"Planning failed: {exc.message}")
        else:
            st.rerun()


def _render_plan(plan: Plan) -> None:
    st.markdown("**Architecture**")
    st.write(plan.architecture_summary or "(no summary)")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Planned changes", len(plan.changes))
    c2.metric("Findings deferred", len(plan.deferred))
    c3.metric("Rejected (invalid)", plan.rejected_changes)
    c4.metric("Model calls", plan.model_calls)
    for note in plan.limitations:
        st.caption(note)
    if plan.rejection_reasons:
        st.caption("Rejected because: " + "; ".join(plan.rejection_reasons))

    if plan.design_decisions:
        st.markdown("**Design decisions**")
        for d in plan.design_decisions:
            st.markdown(f"- **{d.decision}**" + (f": {d.rationale}" if d.rationale else ""))
    st.markdown("**Planned changes** (nothing has been changed yet)")
    st.dataframe(change_rows(plan), hide_index=True, width="stretch")
    if plan.deferred:
        st.markdown("**Deferred findings**")
        for d in plan.deferred:
            st.markdown(f"- {d.finding_id}: {d.reason}")
    if plan.risks:
        st.markdown("**Risks**")
        for r in plan.risks:
            st.markdown(f"- {r}")
    if plan.test_strategy:
        st.markdown("**Test strategy**")
        st.write(plan.test_strategy)
    with st.expander(f"Files sent to the model ({len(plan.files_sent)})"):
        st.write(plan.files_sent or "none")
    tokens_in = plan.prompt_tokens if plan.prompt_tokens is not None else "unknown"
    tokens_out = plan.completion_tokens if plan.completion_tokens is not None else "unknown"
    cost = "unknown" if plan.cost_usd is None else f"${plan.cost_usd:.4f}"
    st.caption(
        f"Provider: {plan.provider} / {plan.model} | prompt {plan.prompt_version} | "
        f"tokens: {tokens_in} in, {tokens_out} out | cost: {cost}"
    )


def _build_tab(service: ProjectService) -> None:
    st.subheader("Step 4: Build")
    run = _selected_run(service, needs="inventory")
    if run is None:
        st.info("Import a repository first (Dashboard).")
        return
    st.caption(f"Run {short_id(run.run_id)} | {run.source} | {state_label(run.state)}")

    if run.build is not None:
        _render_build(run.build)
        return
    if run.plan is None:
        st.info("The plan (step 3) must finish first.")
        return
    if not service.environment_status().llm_configured:
        st.warning(NO_PROVIDER_BANNER)
        return
    if run.state is RunState.PLAN_READY:
        st.info("The plan is ready. Nothing has been written yet.")
        if st.button("Run build", key=f"build_{run.run_id}"):
            try:
                with st.spinner("Building... this can take several minutes."):
                    service.run_build(run.run_id)
            except AseError as exc:
                st.error(f"Build failed: {exc.message}")
            else:
                st.rerun()
    else:
        st.info("This run cannot be built in its current state.")


def _render_build(report: BuildReport) -> None:
    st.warning(
        "Built, NOT verified. The code below was written by an AI model and has not been "
        "run. Step 5 will test it."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Applied", report.applied)
    c2.metric("Failed", report.failed)
    c3.metric("Skipped", report.skipped)
    c4.metric("Model calls", report.model_calls)
    st.caption(
        f"Changes are in the isolated copy `{report.build_dir}`; the original clone is untouched."
    )
    for note in report.limitations:
        st.caption(note)

    for ch in report.changes:
        title = f"{ch.change_id} | {ch.status.upper()} | {ch.action} {ch.path}"
        with st.expander(title, expanded=ch.status == "failed"):
            if ch.status != "applied":
                st.error(ch.reason or "No reason recorded.")
                continue
            st.markdown(
                f"+{ch.added_lines} / -{ch.removed_lines} lines | syntax check: "
                f"{ch.syntax_check.replace('_', ' ')} | attempts: {ch.attempts}"
            )
            st.code(ch.diff or "(no diff)", language="diff")
    tokens_in = report.prompt_tokens if report.prompt_tokens is not None else "unknown"
    tokens_out = report.completion_tokens if report.completion_tokens is not None else "unknown"
    cost = "unknown" if report.cost_usd is None else f"${report.cost_usd:.4f}"
    st.caption(
        f"Provider: {report.provider} / {report.model} | prompt {report.prompt_version} | "
        f"tokens: {tokens_in} in, {tokens_out} out | cost: {cost}"
    )


_OUTCOME_TEXT = {
    "passed": ("success", "PASSED: the project's tests ran in the sandbox and passed."),
    "failed": ("error", "FAILED: a check really failed and the bounded repairs did not fix it."),
    "unverified": (
        "warning",
        "UNVERIFIED: ASE could not prove the code works. This is NOT a pass.",
    ),
}


def _verify_tab(service: ProjectService) -> None:
    st.subheader("Step 5: Test & Verify")
    run = _selected_run(service, needs="inventory")
    if run is None:
        st.info("Import a repository first (Dashboard).")
        return
    st.caption(f"Run {short_id(run.run_id)} | {run.source} | {state_label(run.state)}")

    if run.verification is not None:
        _render_verification(run.verification)
        return
    if run.build is None:
        st.info("The build (step 4) must finish first.")
        return
    env = service.environment_status()
    if not env.code_execution_available:
        st.warning(f"{env.code_execution_note}")
        return
    if run.state is RunState.BUILT:
        if st.button("Run verification", key=f"verify_{run.run_id}"):
            try:
                with st.spinner("Running checks in the sandbox... this can take several minutes."):
                    service.run_verify(run.run_id)
            except AseError as exc:
                st.error(f"Verification failed to start: {exc.message}")
            else:
                st.rerun()
    else:
        st.info("This run cannot be verified in its current state.")


def _render_verification(report: VerifyReport) -> None:
    kind, text = _OUTCOME_TEXT[report.outcome]
    getattr(st, kind)(text)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rounds", len(report.rounds))
    c2.metric("Repairs used", f"{report.repair_attempts_used} of {report.max_repair_attempts}")
    c3.metric("Sandbox", report.sandbox or "-")
    c4.metric("Model calls (repair)", report.model_calls)
    st.caption(f"Network policy: {report.network_policy}")
    for note in report.limitations:
        st.caption(note)

    for rnd in report.rounds:
        st.markdown(f"**Round {rnd.index}: {rnd.outcome.upper()}**")
        st.dataframe(
            [
                {
                    "Check": c.name,
                    "Command": c.command,
                    "Result": c.status.value,
                    "Exit code": "-" if c.exit_code is None else c.exit_code,
                    "Seconds": "-" if c.duration_seconds is None else round(c.duration_seconds, 1),
                }
                for c in rnd.checks
            ],
            hide_index=True,
            width="stretch",
        )
        for reason in rnd.unverified_reasons:
            st.warning(reason)
        for c in rnd.checks:
            if c.output and c.status.value in ("failed", "passed"):
                with st.expander(f"Output of {c.name} ({c.status.value})"):
                    st.code(c.output, language=None)
        if rnd.repaired:
            st.markdown(f"Repair made after round {rnd.index}:")
            for ch in rnd.repaired:
                with st.expander(f"{ch.change_id} | {ch.status.upper()} | {ch.path}"):
                    if ch.status == "applied":
                        st.code(ch.diff or "(no diff)", language="diff")
                    else:
                        st.error(ch.reason)
