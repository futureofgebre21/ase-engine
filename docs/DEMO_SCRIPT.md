# Three-minute demo plan

Goal: show real work, built during the hackathon, with Meta models visibly doing it. Show only
what actually works. Rehearse once with the real keys before recording.

## Before recording
1. `.env` has the Baseten key (and the Modal Endpoint if used); `ASE_ALLOW_CODE_EXECUTION=true`.
2. `python scripts/sandbox_selftest.py` prints "Sandbox self-test: PASSED".
3. Prepare a small **public** repository with a real, simple bug and a test that exposes it
   (for example publish `tests/fixtures/sample_repo` as its own GitHub repository).
4. Do one full dry run so the model/sandbox images are warm (the first run is slower).

## Script (about 3:00)
| Time | Show | Say |
|---|---|---|
| 0:00-0:20 | Dashboard | The problem: reviewing and fixing unfamiliar code is slow. ASE does it end to end, with evidence. |
| 0:20-0:50 | Paste the repo URL, run | Steps 1-3 run automatically: import, audit, plan. Open the Audit tab: every finding cites real code. |
| 0:50-1:20 | "AI models used" table + sidebar | These steps run on Meta's Muse Spark Open: audit and plan via Baseten, build via Modal Endpoints. Calls and tokens are read from the run record. |
| 1:20-1:50 | Build tab | The planned changes as diffs, written into an isolated copy; the original is untouched. |
| 1:50-2:35 | Test & Verify tab | The tests run in a Modal Sandbox with registry-only network. Show the failing round, the repair diff, and the passing round with real exit codes. |
| 2:35-2:50 | Verification outcome | Passed means the tests really passed. Unverified is never shown as passed. |
| 2:50-3:00 | Close | Built during the hackathon with Meta models. Next: GitHub push and deploy with approval. |

## Rules for the recording
- Do not show or claim anything that is not implemented (no push, no deploy).
- Do not show keys or `.env`.
- State the AI-assisted development disclosure in the video description or submission text.
