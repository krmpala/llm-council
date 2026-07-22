# Autonomous cycle {{ITERATION}}

You are one iteration of a supervised continuous Codex workflow.

Repository root: `{{PROJECT_PATH}}`
Current timestamp: `{{TIMESTAMP}}`

Read `codex-run/AUTONOMOUS_AGENT_PROMPT.md` first and obey it.
Then inspect:

- `codex-run/AUTONOMOUS_STATE.md`
- `codex-run/BACKLOG.md`
- `codex-run/last-verification.log` if present
- `git status --short`
- `git log -8 --oneline`

Complete exactly one highest-priority bounded work package.

Important:
- Continue partially completed edits; do not discard them.
- Do not ask for user input.
- Do not add research features until Phase 0 and Phase 1 gates pass.
- Do not merge or modify `master`.
- Do not expose secrets or use live paid provider calls.
- Run focused tests and then relevant full checks.
- Commit only a coherent tested change.
- Update autonomous state/report/backlog before finishing.
- If blocked, document it and choose another safe bounded task where possible.
- If the roadmap is already stable, perform a review/evaluation cycle rather than inventing a feature.
- Büyük dosyaları Get-Content -Raw ile tamamen okuma.
- Önce rg ile ilgili fonksiyonları ve satırları bul.
- Dosyaları en fazla 200-300 satırlık bölümler halinde incele.
- Bir turda tüm repository'yi tekrar tekrar bağlama yükleme.
- AUTONOMOUS_AGENT_PROMPT.md daha önce okunmuşsa yalnızca mevcut fazın ilgili bölümünü incele.
- Tek turda bir sınırlı kod değişikliği ve onun testlerine odaklan.
