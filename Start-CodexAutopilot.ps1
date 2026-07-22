param(
    [string]$ProjectPath = "C:\Users\Pala\Documents\llm-council",
    [int]$DefaultSleepSeconds = 45,
    [int]$StableSleepSeconds = 900,
    [int]$FailureSleepSeconds = 600,
    [switch]$AutoPush
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Log {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$stamp] $Message"
    Write-Host $line
    Add-Content -Path $script:SupervisorLog -Value $line -Encoding UTF8
}

function Invoke-LoggedCommand {
    param(
        [string]$Label,
        [scriptblock]$Command,
        [string]$LogPath
    )
    "===== $Label : $(Get-Date -Format o) =====" | Add-Content $LogPath -Encoding UTF8
    try {
        & $Command *>&1 | Tee-Object -FilePath $LogPath -Append
        $exitCode = $LASTEXITCODE
        if ($null -eq $exitCode) { $exitCode = 0 }
        "EXIT_CODE=$exitCode" | Add-Content $LogPath -Encoding UTF8
        return [int]$exitCode
    }
    catch {
        "POWERSHELL_ERROR=$($_.Exception.Message)" | Add-Content $LogPath -Encoding UTF8
        return 1
    }
}

if (-not (Test-Path $ProjectPath)) {
    throw "Project path not found: $ProjectPath"
}
Set-Location $ProjectPath

if (-not (Test-Path ".git")) {
    throw "This supervisor requires an existing Git repository."
}
$currentBranch = (git branch --show-current).Trim()
if ([string]::IsNullOrWhiteSpace($currentBranch)) {
    throw "Could not determine the current Git branch."
}
if ($currentBranch -in @("master", "main")) {
    throw "Refusing unattended work on protected branch '$currentBranch'. Switch to a feature branch first."
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw "Codex CLI was not found. Install/update it and sign in before starting."
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm was not found."
}

$runDir = Join-Path $ProjectPath "codex-run"
$logDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force $runDir, $logDir | Out-Null

$script:SupervisorLog = Join-Path $runDir "supervisor.log"
$masterPrompt = Join-Path $runDir "AUTONOMOUS_AGENT_PROMPT.md"
$cycleTemplate = Join-Path $runDir "CYCLE_TEMPLATE.md"
$schemaPath = Join-Path $runDir "cycle-output.schema.json"
$statePath = Join-Path $runDir "AUTONOMOUS_STATE.md"
$backlogPath = Join-Path $runDir "BACKLOG.md"
$reportPath = Join-Path $runDir "AUTONOMOUS_REPORT.md"
$issuesPath = Join-Path $runDir "KNOWN_ISSUES.md"
$stopPath = Join-Path $runDir "STOP"
$pausePath = Join-Path $runDir "PAUSE"

foreach ($required in @($masterPrompt, $cycleTemplate, $schemaPath)) {
    if (-not (Test-Path $required)) {
        throw "Missing supervisor file: $required"
    }
}

if (-not (Test-Path $statePath)) {
@"
# Autonomous State

- Current phase: Phase 0
- Status: Resume interrupted OpenRouter parser and multilingual quality work.
- Last verified tests: Not yet recorded by supervisor.
- Next priority: Inspect current dirty worktree and finish the exact Phase 0 acceptance criteria.
"@ | Set-Content $statePath -Encoding UTF8
}
if (-not (Test-Path $backlogPath)) {
@"
# Backlog

1. Finish interrupted provider response parser and multilingual quality implementation.
2. Add/repair regression tests for malformed responses and target-language handling.
3. Run all backend/frontend checks and commit Phase 0.
4. Proceed through gated phases in AUTONOMOUS_AGENT_PROMPT.md.
"@ | Set-Content $backlogPath -Encoding UTF8
}
if (-not (Test-Path $reportPath)) {
    "# Autonomous Report`n" | Set-Content $reportPath -Encoding UTF8
}
if (-not (Test-Path $issuesPath)) {
    "# Known Issues`n" | Set-Content $issuesPath -Encoding UTF8
}

# Keep the PC awake while this PowerShell process is running.
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class AwakeState {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
$ES_CONTINUOUS = [uint32]0x80000000
$ES_SYSTEM_REQUIRED = [uint32]0x00000001
$ES_AWAYMODE_REQUIRED = [uint32]0x00000040
[void][AwakeState]::SetThreadExecutionState(
    $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_AWAYMODE_REQUIRED
)

try {
    Write-Log "Codex autopilot started in $ProjectPath"
    Write-Log "Stop with Ctrl+C or create $stopPath"
    Write-Log "Pause by creating $pausePath; resume by deleting it."

    # Diagnostics are informative; a failure does not destroy the loop.
    try {
        codex --version *>&1 | Tee-Object -FilePath (Join-Path $runDir "codex-version.log")
    }
    catch {
        Write-Log "Codex version check reported an issue: $($_.Exception.Message)"
    }

    $iteration = 0
    $consecutiveFailures = 0

    while ($true) {
        if (Test-Path $stopPath) {
            Write-Log "STOP file detected. Exiting cleanly."
            break
        }

        while (Test-Path $pausePath) {
            Write-Log "PAUSE file detected. Sleeping for 60 seconds."
            Start-Sleep -Seconds 60
            if (Test-Path $stopPath) { break }
        }
        if (Test-Path $stopPath) { break }

        $iteration++
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $cycleDir = Join-Path $logDir ("cycle-{0:D4}-{1}" -f $iteration, $stamp)
        New-Item -ItemType Directory -Force $cycleDir | Out-Null

        $promptPath = Join-Path $cycleDir "prompt.md"
        $jsonlPath = Join-Path $cycleDir "events.jsonl"
        $stderrPath = Join-Path $cycleDir "stderr.log"
        $finalPath = Join-Path $cycleDir "final.json"
        $verificationPath = Join-Path $cycleDir "verification.log"

        $templateText = Get-Content -Raw $cycleTemplate
        $cyclePrompt = $templateText.Replace("{{ITERATION}}", [string]$iteration)
        $cyclePrompt = $cyclePrompt.Replace("{{PROJECT_PATH}}", $ProjectPath)
        $cyclePrompt = $cyclePrompt.Replace("{{TIMESTAMP}}", (Get-Date -Format o))
        $cyclePrompt | Set-Content $promptPath -Encoding UTF8

        $headBefore = git rev-parse HEAD
        $statusBefore = git status --porcelain

        Write-Log "Cycle $iteration starting. HEAD=$headBefore"

        # codex exec is intentionally one bounded turn. This outer loop starts the next turn.
        try {
            Get-Content -Raw $promptPath |
                & codex exec `
                    --sandbox workspace-write `
                    --json `
                    --output-schema $schemaPath `
                    --output-last-message $finalPath `
                    - 2> $stderrPath |
                Tee-Object -FilePath $jsonlPath
            $codexExit = $LASTEXITCODE
            if ($null -eq $codexExit) { $codexExit = 0 }
        }
        catch {
            $codexExit = 1
            $_ | Out-String | Add-Content $stderrPath -Encoding UTF8
        }

        Write-Log "Cycle $iteration Codex exit code: $codexExit"

        # Independent verification. These logs feed the next cycle.
        $verifyResults = @()
        $verifyResults += Invoke-LoggedCommand "backend pytest" {
            uv run pytest
        } $verificationPath
        $verifyResults += Invoke-LoggedCommand "backend compileall" {
            uv run python -m compileall backend
        } $verificationPath

        Push-Location (Join-Path $ProjectPath "frontend")
        try {
            $verifyResults += Invoke-LoggedCommand "frontend tests" {
                npm test
            } $verificationPath
            $verifyResults += Invoke-LoggedCommand "frontend lint" {
                npm run lint
            } $verificationPath
            $verifyResults += Invoke-LoggedCommand "frontend build" {
                npm run build
            } $verificationPath
        }
        finally {
            Pop-Location
        }

        Copy-Item $verificationPath (Join-Path $runDir "last-verification.log") -Force

        $headAfter = git rev-parse HEAD
        $statusAfter = git status --porcelain
        $allVerificationPassed = -not ($verifyResults | Where-Object { $_ -ne 0 })

        $cycleLeaf = Split-Path $cycleDir -Leaf
        $reportEntry = @(
            ""
            ("## Supervisor cycle {0} - {1}" -f $iteration, (Get-Date -Format o))
            ""
            ("- Codex exit code: {0}" -f $codexExit)
            ("- HEAD before: {0}" -f $headBefore)
            ("- HEAD after: {0}" -f $headAfter)
            ("- Independent full verification passed: {0}" -f $allVerificationPassed)
            ("- Final report: codex-run/logs/{0}/final.json" -f $cycleLeaf)
            ("- Verification log: codex-run/logs/{0}/verification.log" -f $cycleLeaf)
            ""
        ) -join [Environment]::NewLine
        Add-Content -Path $reportPath -Value $reportEntry -Encoding UTF8

        if ($AutoPush -and $allVerificationPassed -and ($headAfter -ne $headBefore)) {
            Write-Log "Verified commit detected; attempting git push origin HEAD."
            try {
                git push origin HEAD *>&1 | Tee-Object -FilePath (Join-Path $cycleDir "push.log")
            }
            catch {
                Write-Log "Push failed; continuing without destructive recovery."
            }
        }

        $cycleStatus = "failed"
        $recommendedSleep = $DefaultSleepSeconds
        if (Test-Path $finalPath) {
            try {
                $finalObject = Get-Content -Raw $finalPath | ConvertFrom-Json
                $cycleStatus = [string]$finalObject.cycle_status
                if ($finalObject.recommended_sleep_seconds) {
                    $recommendedSleep = [int]$finalObject.recommended_sleep_seconds
                }
            }
            catch {
                Write-Log "Final JSON could not be parsed; next cycle will inspect logs."
            }
        }

        if (($codexExit -eq 0) -and $allVerificationPassed) {
            $consecutiveFailures = 0
            if ($cycleStatus -eq "stable") {
                $sleepSeconds = [Math]::Max($StableSleepSeconds, $recommendedSleep)
            }
            else {
                $sleepSeconds = [Math]::Max(10, $recommendedSleep)
            }
        }
        else {
            $consecutiveFailures++
            $sleepSeconds = [Math]::Min(
                3600,
                $FailureSleepSeconds * [Math]::Max(1, $consecutiveFailures)
            )
        }

        Write-Log "Cycle $iteration finished: status=$cycleStatus verification=$allVerificationPassed. Sleeping $sleepSeconds seconds."
        Start-Sleep -Seconds $sleepSeconds
    }
}
finally {
    [void][AwakeState]::SetThreadExecutionState($ES_CONTINUOUS)
    Write-Log "Codex autopilot stopped."
}
