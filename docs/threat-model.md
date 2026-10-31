# Threat Model (Phase 1)

**Assets:** user's secrets (API/GitHub tokens), host machine, user's repositories, trustworthiness of reports.
**Untrusted inputs:** repo contents (code, README, comments, dependency metadata), repo URLs,
model output, command output.

| Threat | Mitigation | Status |
|---|---|---|
| Malicious repo URL / SSRF | github.com-only HTTPS allowlist, no creds/ports/queries (`security/urls.py`) | Implemented + tested |
| Path traversal | `resolve_within` after resolving | Implemented + tested |
| Malicious clone (hooks, submodules, symlinks, odd protocols) | https-only, no submodules, `core.symlinks=false`, minimal env (no tokens passed to git), timeout, post-clone symlink scan, cleanup on failure (`repository/gitclone.py`, `importer.py`) | Implemented; live clone only verified on a real machine |
| Symlink escape | resolve-before-check; `find_escaping_symlinks` post-import scan | Implemented + tested |
| Secret leakage in logs/prompts/UI | `redact`, `is_sensitive_path`, `SecretStr` config | Implemented (best-effort) |
| Prompt injection from repo text | Repo files wrapped in nonce-tagged data blocks (cannot close the wrapper), system prompt marks them untrusted, model output is a data-only schema (no actions), every claim re-verified against real files | Implemented (prompt side tested with mock; real-model resistance unmeasured) |
| Hallucinated / fabricated evidence | `agent/evidence.py` keeps a finding only if a cited file (that was actually sent), line range and quote exist; others are discarded and counted | Implemented + tested |
| Secrets sent to a provider | Only files from `read_file_for_model` (sensitive/binary/symlink excluded, redacted) are sent | Implemented + tested |
| Arbitrary command execution | Typed operations/allowlist; no model-written shell | Planned (Phase 6-7) |
| Running malicious repo code | Docker sandbox, no network, no host mounts; else execution disabled | Planned (Phase 7); disabled by default |
| Malicious install scripts | No automatic dependency install without policy + approval | Planned |
| Unsafe archive extraction | `git clone` only; no archive extraction exists | Implemented (by omission) |
| Unauthorized push/deploy | Approval record required; least-privilege token | Planned (Phase 9) |
| Resource exhaustion | Limits in `Settings`. File count, total size, per-file size, and clone timeout enforced now; model/repair/exec limits planned | Partially implemented |
| Fabricated success claims | Evidence-gated models, state machine | Implemented + tested |

**Residual risks:** TOCTOU between path check and use; regex redaction misses novel secret formats;
a sandbox is only as strong as its configuration.

**Phase 4 residual risks:** size/file limits are enforced *after* the clone finishes, so a huge repo can briefly use disk (the clone timeout bounds this); skipped generated directories (e.g. `node_modules`) are not counted toward limits; framework hints are keyword matches, not proof.

**Phase 5 residual risks:** a real model may still be steered by injected text into false (but evidence-backed) findings; verification proves code exists at a location, not that the interpretation is right, so AI findings stay `unverified`. Cancellation of an in-flight model call is not implemented.
