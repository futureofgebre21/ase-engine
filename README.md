# ASE — Autonomous Software Engineer

An AI agent that imports or creates a software project, audits it with evidence, proposes
reviewable patches, verifies them in an isolated environment, and only pushes or deploys after
explicit approval.

**Status: steps 1-5 are real; step 6 (push and deploy) is not built yet.**

Working today, automatically after you submit a public GitHub URL (any language):
1. **Analyze and import**: hardened `git clone`, inventory (languages, ecosystems, manifests,
   tests, entry points, build/CI files, exclusions).
2. **Audit**: AI review as senior software, DevOps and full-stack engineers; every finding must
   cite code that really exists.
3. **Architecture and plan**: AI design plus a change list, validated by code.
4. **Build**: changes are written into an isolated copy; the untouched clone is the baseline.
   Existing files change only by exact search-and-replace on their real text; generated text is
   rejected if it contains secrets or (Python/JSON/TOML) a syntax error.
5. **Test and verify**: the build copy runs in an isolated **Modal Sandbox** (network limited to
   package registries, no secrets). Real exit codes decide the result. Failing checks trigger a
   bounded repair loop that can edit only the files the plan approved. Results are **passed**,
   **failed** or **unverified**; automatic verification supports Python and Node, everything
   else is honestly reported as unverified. Needs `ASE_ALLOW_CODE_EXECUTION=true`.

AI models: Meta's Muse Spark Open, through Baseten and (optionally) Modal Endpoints. The UI
shows which model each step really used. See `docs/HACKATHON.md`.

Not built yet: GitHub push and deploy. The app shows that tab as "not built yet".

Rules this project follows: no mock or demo data in the product (results come only from real
repository files, real model replies and real command output); an unconfigured AI provider is
reported as such; test doubles exist only under `tests/`.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

## Setup on Windows (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```
Run the commands one per line (older PowerShell does not support `&&`). Some security tests create
symlinks; on Windows they are skipped unless Developer Mode is enabled.

## Run the app
```bash
streamlit run app/main.py
```

## Check the sandbox (once)
```
modal setup
python scripts/sandbox_selftest.py
```

## Check quality
```bash
ruff check .
ruff format --check .
pytest
```

## Docs
- `docs/architecture.md` — MVP stories, acceptance criteria, design decisions
- `docs/threat-model.md` — trust boundaries and mitigations

## AI model setup
Copy `.env.example` to `.env`, then put your key in `ASE_LLM_API_KEY`. Do not share the key or
commit `.env`. The adapter (`src/ase/llm/openai_compatible.py`) works with any OpenAI-compatible
`/chat/completions` server and uses only the Python standard library.

## Troubleshooting
- `git clone failed ... CRYPT_E_REVOCATION_OFFLINE` (Windows): your network blocks certificate-revocation
  servers. Set `ASE_GIT_CHECK_CERT_REVOCATION=false` (weakens one TLS check; only use for public repos).

## Known limitations
- No sandbox yet: code execution is disabled by default (`ASE_ALLOW_CODE_EXECUTION=false`).
- LLM provider, hosting, and hackathon eligibility are **unverified** (see architecture doc).
- Redaction and URL checks are best-effort deterministic layers, not complete protection.
