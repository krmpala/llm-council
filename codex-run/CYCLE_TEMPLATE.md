# Autonomous cycle {{ITERATION}}

Repository: {{PROJECT_PATH}}
Timestamp: {{TIMESTAMP}}

Obey codex-run/AUTONOMOUS_AGENT_PROMPT.md.

IMPORTANT WINDOWS RULE:
- Use `Get-Content -Encoding UTF8`; never use plain `Get-Content`.
- Do not print entire large files.
- Use `rg` to locate symbols and inspect only relevant 200-300 line ranges.
- Keep all JSON progress summaries clear and understandable in Turkish.

Read the current phase/state/backlog, git status, recent commits, and last
verification result. Complete exactly one highest-priority bounded work package.

Do not ask for user input. Do not touch AGENTS.md. Do not merge to master/main.
Do not expose secrets or use live paid calls. Commit only coherent tested work.
If blocked, document the exact blocker and continue with another safe bounded
task where possible. End with valid JSON matching the configured schema.


UNATTENDED REPAIR LOOP:
- A failing test is not a reason to stop. Treat it as the next repair input.
- Read codex-run/last-verification.log first.
- Fix the smallest coherent cluster of failures.
- If one approach fails, record why and try a safer alternative in the next cycle.
- Never wait for user confirmation.
- Do not merely report test failures; make a concrete repair attempt.
- Keep going until the current phase gate is green.
