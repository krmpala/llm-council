# LLM Council Autonomous Codex Objective V3

## Mission

Continuously and safely improve the llm-council repository. Each Codex run must
complete one bounded, high-value iteration, test it, record state, and exit.
The PowerShell supervisor will start the next iteration.

Do not ask the user questions during unattended work. Make conservative,
backward-compatible decisions. Never deploy, merge to master/main, force-push,
purchase services, expose secrets, or modify .env.

All progress summaries returned through the JSON output schema should be written
in clear Turkish. Source code, identifiers, and commit messages may remain
English.

## Critical Windows encoding rule

This repository is being processed by Windows PowerShell 5.1.

- NEVER read a text file with plain `Get-Content`.
- Use `Get-Content -Raw -Encoding UTF8 <path>`.
- For partial reads, use `Get-Content -Encoding UTF8 <path> | Select-Object ...`.
- Prefer Python `Path(...).read_text(encoding="utf-8")` for complex text work.
- Write text as UTF-8.
- If text contains obvious mojibake or corrupted encoding, do not copy that
  damaged text into source files. Re-read the original file as UTF-8.
- Never dump a very large file into the context. Use `rg` first, then inspect
  no more than 200-300 relevant lines.

## Repository rules

- Work only on the current non-protected feature branch.
- Preserve the three-stage council:
  1. individual responses,
  2. anonymous peer evaluation,
  3. chairman synthesis.
- Continue useful partial implementation; do not discard it.
- Use mocks/fakes for provider tests.
- Do not run large batches of real provider requests.
- Do not read or print .env or API keys.
- Do not use git reset --hard, force push, history rewriting, or destructive
  cleanup.
- Commit only coherent work whose relevant tests pass.
- Keep AGENTS.md untouched and untracked unless the user explicitly changes
  that decision.

## Every iteration

1. Read this roadmap, AUTONOMOUS_STATE.md, BACKLOG.md, recent commits, git
   status, and last-verification.log when present.
2. Select exactly one bounded work package.
3. Inspect only the relevant functions/files.
4. Implement the smallest safe change.
5. Run focused tests.
6. Run the relevant full checks before a phase commit:
   - uv run pytest
   - uv run python -m compileall backend
   - frontend: npm test
   - frontend: npm run lint
   - frontend: npm run build
7. Update AUTONOMOUS_STATE.md, BACKLOG.md, AUTONOMOUS_REPORT.md, and
   KNOWN_ISSUES.md.
8. Create one descriptive commit when the package is coherent and tested.
9. Return a concise status. JSON is preferred but plain text is acceptable.

## Phase 0 - Provider parsing and multilingual quality

Finish this phase before adding research features.

Acceptance criteria:

- No direct unsafe access to OpenRouter choices/message/content fields.
- HTTP 200 bodies containing an error object are treated as provider errors.
- Controlled error types include:
  invalid_json, empty_response, missing_choices, empty_choices,
  missing_message, missing_content, empty_content, provider_error_payload,
  malformed_response, network_error, timeout, rate_limited,
  authentication_error, and model_not_found.
- Raw KeyError/IndexError messages such as `'choices'` never reach the UI.
- Retryable errors include 429, 502, 503, 504, timeout, network errors, empty
  upstream responses, empty choices, and temporary malformed responses.
- Seat fallback metadata clearly records primary_model, actual_model,
  fallback_used, and fallback_index.
- A Stage 1 response is successful only after schema, empty-content,
  truncation, target-language, and text-quality validation.
- A failed language/quality response gets at most one from-scratch repair.
- A failed repair does not count as a successful council member and is not sent
  to Stage 2 or the chairman.
- Resolve target language by explicit request first, then detected message
  language, then conversation language, then safe default.
- Support at least tr, en, de, fr, es, ar, ru, and zh.
- Carry the target language through Stage 1, repairs, Stage 2, JSON repair,
  chairman synthesis, chairman review, and title generation.
- Technical names such as Python, PyTorch, TensorFlow, GitHub, Kaggle, NumPy,
  Coursera, and FastAPI must not trigger false language failures.
- Stage 2 evaluators always reach completed, failed, invalid_output, or
  interrupted; the UI never remains in an infinite scoring state.
- Chairman review failure preserves the first valid synthesis.
- Frontend shows friendly normalized provider and language-quality errors.
- Old conversation data is normalized safely.
- Remove duplicate legacy implementations rather than leaving multiple
  extract_user_constraints or language-instruction functions active.
- Add regression tests for Turkish, English, German, explicit language
  override, malformed provider payloads, repair success/failure, truncation,
  Stage 2 terminal states, and chairman fallback.
- Complete full backend/frontend verification.
- Commit when complete:
  Stabilize provider parsing and multilingual quality

## Phase 1 - Deterministic demo and reliability

Start only after Phase 0 passes.

- Add a deterministic mock/demo provider requiring no real API key.
- Cover three successful seats, 429 plus fallback, malformed response,
  wrong-language repair, Stage 2 timeout/invalid JSON, and chairman retry.
- Add a complete automated smoke flow.
- Ensure SSE terminal states and restored interrupted conversations resolve.
- Add health/readiness diagnostics.
- Document demo commands in README and .env.example.
- Complete full verification.
- Commit:
  Add deterministic council demo and smoke coverage

## Phase 2 - Evidence-based research MVP

Start only after Phases 0 and 1 pass.

- Research modes: off, auto, required.
- SearchProvider interface with SearXNG and Tavily adapters.
- Missing configuration degrades gracefully.
- At most four planned non-duplicate queries.
- Persist normalized source metadata and a shared evidence pack with S1-style
  identifiers.
- Deduplicate sources and cap at ten.
- Prefer official, primary, and academic sources.
- Add safe HTTP fetching with SSRF protection, redirect validation, timeout,
  and body-size limits.
- Validate citations and nonexistent source IDs.
- Add 30-minute cache.
- Add research progress and Sources UI.
- Use mocks in tests; no live paid API requirement.
- Complete full verification.
- Commit:
  Add evidence based research workflow

## Phase 3 - Product hardening

- Backward compatibility tests for old conversation JSON.
- Error boundaries and reconnect behavior.
- Accessibility and keyboard navigation.
- Clear loading, empty, degraded, and blocked states.
- Log redaction and dependency/security review.
- Better Windows startup instructions.
- Architecture and manual QA documentation.
- Complete full verification.
- Commit:
  Harden council UX compatibility and operations

## Phase 4 - Stable review loop

After Phases 0-3, do not invent major features. Re-run tests, inspect flaky or
risky areas, review security/correctness/race conditions, and improve tests or
documentation only for concrete findings. If no actionable issue exists, return
stable and recommend a longer sleep.


## Unattended self-repair policy

- Test failures and individual Codex exit failures are normal repair signals.
- Never stop merely because tests are red.
- The supervisor will launch another cycle. Preserve useful partial work and
  make the next bounded repair attempt.
- Read `codex-run/last-verification.log` before selecting work.
- Prefer fixing production behavior; update obsolete tests only when the new
  intended behavior is clearly documented by this roadmap.
- Do not wait for approval or user input.
