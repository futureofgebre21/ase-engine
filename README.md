# ASE — Autonomous Software Engineer

An AI agent that imports or creates a software project, audits it with evidence, proposes
reviewable patches, verifies them in an isolated environment, and only pushes or deploys after
explicit approval.

**Status: steps 1-4 are real; steps 5-6 are not built yet.**

Working today, automatically after you submit a public GitHub URL (any language):
1. **Analyze and import**: hardened `git clone`, inventory (languages, ecosystems, manifests,
   tests, entry points, build/CI files, exclusions).
2. **Audit**: AI review as senior software, DevOps and full-stack engineers. Every finding must
   cite code that really exists; unverifiable findings are discarded and counted.
3. **Architecture and plan**: AI design plus a list of changes. Code, not the model, validates
   every change (safe paths, no secrets/generated/binary/.git targets, modify only files the
   model was shown, every change tied to a real finding, every finding planned or deferred).
4. **Build**: the planned changes are written into an isolated copy (`workspaces/build/<run>`);
   the untouched clone (`workspaces/repos/<run>`) is the baseline for diffs and rollback.
   Existing files change only by exact search-and-replace on their real text, so secrets that
   were hidden from the model are never overwritten. Generated text is rejected if it contains
   secrets, the redaction marker, or (for Python/JSON/TOML) a syntax error; a rejected attempt is
   retried once with the reason. Every applied change is shown as a diff.

The result of step 4 is **built, not verified**: nothing has been run yet.

Not built yet: test and verify in a sandbox (5), GitHub push and deploy (6). The app shows these
tabs as "not built yet"; it has no fake buttons.

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
