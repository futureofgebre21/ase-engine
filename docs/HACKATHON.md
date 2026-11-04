# Meta Global AI Developer Hackathon: how ASE fits the official guide

This file maps ASE to the official Participant Guide. It states only facts about this
repository. Items marked **TO FILL** must be completed by the team before submitting.

## Track and problem statement
ASE (Autonomous Software Engineer) is an agent that imports a repository, audits it, plans and
builds changes, and verifies them in an isolated sandbox. It fits **Push the Frontier**
(software): it gives a machine a new capability in the domain of software engineering.
**TO FILL:** confirm the track chosen on the submission form.

## Meta models used (Stage One: "reasonably used Meta's models")
| Where | Meta model | How it is reached |
|---|---|---|
| Step 2 audit | Muse Spark Open (`meta-models/muse-spark-open-1026`) | Baseten Model APIs |
| Step 3 plan | Muse Spark Open | Baseten Model APIs |
| Step 4 build, step 5 repair | Muse Spark Open | Modal Endpoints (shared endpoint), if configured |

The mapping is set in `.env` (`ASE_LLM_*` and `ASE_LLM2_*`). The Dashboard shows, for each run,
the provider, model, number of calls and token counts **read from the recorded reports**, and the
sidebar shows the configured route per step. No other AI model is used by the product.

## What ASE does and does not claim (honesty rules)
- Every audit finding must cite code that exists; unverifiable findings are discarded and counted.
- A plan is validated by code (safe paths, no secrets, tied to real findings).
- Code is written only into an isolated copy; the original clone is the baseline for diffs.
- "Passed" means the project's own tests ran in the sandbox and passed. Missing tests, an
  unsupported ecosystem, or a sandbox problem is reported as **unverified**, never as passed.
- Automatic verification supports **Python and Node** projects. Other languages are audited,
  planned and built, but their tests are reported as unverified.
- Push to GitHub and deploy (step 6) are not implemented yet.

## Rules checklist (from the guide)
- [ ] Repository is **public** and contains no secrets (`.env` is git-ignored; check with
      `git log -p` before publishing).
- [ ] Team of at most four; everyone who worked on the project is listed on the submission.
- [ ] Three-minute demo video highlighting only what was built during the hackathon
      (`docs/DEMO_SCRIPT.md`).
- [ ] Submit on the CV platform by **Wed Nov 4, 9:00 PM ET** (one submission per team).
- [ ] Not an "anti-project" (generic RAG app, image analyzer, medical advice, etc.): ASE is a
      software-engineering agent.
- [ ] Claim Meta Model API credits (optional, due Nov 4 9 PM ET) if you use that API.

## What was built during the hackathon (required disclosure)
The guide requires that the demo highlight only work built during the event and that original
contributions be clearly identified. The building period is **Oct 27 to Nov 4**.

**TO FILL (be exact; use `git log --date=iso`):**
- Work that existed before Oct 27 (for example the first scaffold: configuration, domain
  models, run state machine, path/URL safety helpers, first Streamlit shell) and its dates.
- Work done during the building period (for example hardened import and inventory, AI audit,
  plan validation, build, sandbox verification, multi-provider routing).

Commit early and often, with honest dates. Do not rewrite history.

## AI-assisted development (disclose in the submission)
Large parts of this code base were written with an AI assistant (Claude, by Anthropic) acting as
a pair programmer, under the founder's direction and review. **At runtime the product uses only
Meta models.** Check the hackathon rules on AI-assisted development and state this plainly in
the submission text.
