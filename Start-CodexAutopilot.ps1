param(
    [string]$ProjectPath = "C:\Users\Pala\Documents\llm-council",
    [int]$DefaultSleepSeconds = 45,
    [int]$StableSleepSeconds = 900,
    [int]$FailureSleepSeconds = 90,
    [switch]$AutoPush,
    [switch]$Once,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Windows PowerShell 5.1 UTF-8 compatibility.
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NO_COLOR = "1"
try { & chcp.com 65001 | Out-Null } catch {}

function Repair-Mojibake {
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrEmpty($Text)) {
        return $Text
    }

    # Typical Windows PowerShell/OEM Turkish mojibake:
    # IÔÇÖll -> I’ll, ger├ğek -> gerçek, kap─▒s─▒ -> kapısı
    if ($Text -match "[ÔÇ├┼─]") {
        try {
            $cp857 = [System.Text.Encoding]::GetEncoding(857)
            $bytes = $cp857.GetBytes($Text)
            $fixed = [System.Text.Encoding]::UTF8.GetString($bytes)

            $beforeCount = ([regex]::Matches($Text, "[ÔÇ├┼─]")).Count
            $afterCount = ([regex]::Matches($fixed, "[ÔÇ├┼─]")).Count
            if ($afterCount -lt $beforeCount) {
                return $fixed
            }
        }
        catch {
            # Keep the original text if conversion is not possible.
        }
    }

    return $Text
}

function Write-Ui {
    param(
        [string]$Kind,
        [string]$Message
    )

    $messageText = Repair-Mojibake $Message
    $stamp = Get-Date -Format "HH:mm:ss"

    $color = "Gray"
    switch ($Kind) {
        "BASLADI" { $color = "Cyan" }
        "CODEX"   { $color = "White" }
        "PLAN"    { $color = "Cyan" }
        "KOMUT"   { $color = "DarkCyan" }
        "DOSYA"   { $color = "DarkYellow" }
        "TEST"    { $color = "Yellow" }
        "GECTI"   { $color = "Green" }
        "HATA"    { $color = "Red" }
        "UYARI"   { $color = "Yellow" }
        "BILGI"   { $color = "Gray" }
        "BEKLE"   { $color = "DarkGray" }
    }

    Write-Host ("[{0}] [{1}] {2}" -f $stamp, $Kind, $messageText) -ForegroundColor $color

    if ($script:SupervisorLog) {
        Add-Content -Path $script:SupervisorLog `
            -Value ("[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Kind, $messageText) `
            -Encoding UTF8
    }
}

function Get-ShortCommand {
    param([AllowNull()][string]$Command)

    if ([string]::IsNullOrWhiteSpace($Command)) {
        return "(komut bilgisi yok)"
    }

    $clean = Repair-Mojibake $Command
    $clean = $clean -replace '^".*?powershell\.exe"\s+-Command\s+', ''
    $clean = $clean.Trim("'`" ")
    if ($clean.Length -gt 220) {
        return $clean.Substring(0, 217) + "..."
    }
    return $clean
}

function Show-TextTail {
    param(
        [AllowNull()][string]$Text,
        [int]$LineCount = 12
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return
    }

    $fixed = Repair-Mojibake $Text
    $lines = @($fixed -split "\r?\n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $start = [Math]::Max(0, $lines.Count - $LineCount)
    for ($i = $start; $i -lt $lines.Count; $i++) {
        Write-Host ("           {0}" -f $lines[$i]) -ForegroundColor DarkGray
    }
}

function Show-CodexEvent {
    param([string]$Line)

    if ([string]::IsNullOrWhiteSpace($Line)) {
        return
    }

    try {
        $event = $Line | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        Write-Ui "CODEX" $Line
        return
    }

    switch ([string]$event.type) {
        "thread.started" {
            Write-Ui "BASLADI" ("Codex oturumu acildi: {0}" -f $event.thread_id)
        }
        "turn.started" {
            Write-Ui "PLAN" "Codex yeni calisma turuna basladi."
        }
        "turn.completed" {
            Write-Ui "GECTI" "Codex calisma turunu tamamladı."
        }
        "turn.failed" {
            Write-Ui "HATA" "Codex calisma turu basarisiz oldu."
            if ($event.error.message) {
                Write-Ui "HATA" ([string]$event.error.message)
            }
        }
        "error" {
            $errorMessage = [string]$event.message
            if ([string]::IsNullOrWhiteSpace($errorMessage) -and $event.error) {
                $errorMessage = [string]$event.error
            }
            Write-Ui "HATA" $errorMessage
        }
        "item.started" {
            $itemType = [string]$event.item.type
            if ($itemType -eq "command_execution") {
                Write-Ui "KOMUT" (Get-ShortCommand ([string]$event.item.command))
            }
            elseif ($itemType -eq "file_change") {
                Write-Ui "DOSYA" "Dosya degisikligi hazirlaniyor."
            }
            elseif ($itemType -eq "mcp_tool_call") {
                Write-Ui "KOMUT" "Bir arac cagrisi calistiriliyor."
            }
        }
        "item.completed" {
            $itemType = [string]$event.item.type

            if ($itemType -eq "agent_message") {
                $text = Repair-Mojibake ([string]$event.item.text)
                try {
                    $status = $text | ConvertFrom-Json -ErrorAction Stop
                    if ($status.summary) {
                        Write-Ui "CODEX" ([string]$status.summary)
                    }
                    if ($status.next_priority) {
                        Write-Ui "PLAN" ("Siradaki hedef: {0}" -f [string]$status.next_priority)
                    }
                    if ($status.blockers -and $status.blockers.Count -gt 0) {
                        foreach ($blocker in $status.blockers) {
                            Write-Ui "UYARI" ("Engel: {0}" -f [string]$blocker)
                        }
                    }
                }
                catch {
                    Write-Ui "CODEX" $text
                }
            }
            elseif ($itemType -eq "command_execution") {
                $commandText = Get-ShortCommand ([string]$event.item.command)
                $exitCode = $event.item.exit_code
                if ($exitCode -eq 0) {
                    Write-Ui "GECTI" ("Komut tamamlandi: {0}" -f $commandText)
                }
                else {
                    Write-Ui "HATA" ("Komut basarisiz (exit {0}): {1}" -f $exitCode, $commandText)
                    Show-TextTail ([string]$event.item.aggregated_output) 10
                }
            }
            elseif ($itemType -eq "file_change") {
                Write-Ui "DOSYA" "Dosya degisikligi tamamlandi."
            }
            elseif ($itemType -eq "mcp_tool_call") {
                Write-Ui "GECTI" "Arac cagrisi tamamlandi."
            }
        }
    }
}

function Invoke-VerificationCommand {
    param(
        [string]$Label,
        [string]$WorkingDirectory,
        [string]$CommandLine,
        [string]$LogPath
    )

    Write-Ui "TEST" ("{0} baslatildi: {1}" -f $Label, $CommandLine)
    $watch = [System.Diagnostics.Stopwatch]::StartNew()

    $safeName = ($Label -replace "[^A-Za-z0-9_-]", "_")
    $stdoutPath = Join-Path $env:TEMP ("codex-autopilot-{0}-{1}-stdout.log" -f $safeName, [guid]::NewGuid().ToString("N"))
    $stderrPath = Join-Path $env:TEMP ("codex-autopilot-{0}-{1}-stderr.log" -f $safeName, [guid]::NewGuid().ToString("N"))

    Add-Content -Path $LogPath `
        -Value ("===== {0} : {1} =====`nCOMMAND={2}" -f $Label, (Get-Date -Format o), $CommandLine) `
        -Encoding UTF8

    try {
        # Run native commands outside the current PowerShell error stream.
        # PowerShell 5.1 otherwise turns harmless stderr warnings into
        # NativeCommandError when ErrorActionPreference is Stop.
        $process = Start-Process `
            -FilePath "cmd.exe" `
            -ArgumentList @("/d", "/s", "/c", $CommandLine) `
            -WorkingDirectory $WorkingDirectory `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $exitCode = [int]$process.ExitCode
    }
    catch {
        $exitCode = 1
        [System.IO.File]::WriteAllText(
            $stderrPath,
            $_.Exception.Message,
            (New-Object System.Text.UTF8Encoding($false))
        )
    }
    finally {
        $watch.Stop()
    }

    $stdoutText = ""
    $stderrText = ""

    if (Test-Path $stdoutPath) {
        $stdoutText = [System.IO.File]::ReadAllText($stdoutPath, [System.Text.Encoding]::UTF8)
    }
    if (Test-Path $stderrPath) {
        $stderrText = [System.IO.File]::ReadAllText($stderrPath, [System.Text.Encoding]::UTF8)
    }

    $combinedText = @($stdoutText, $stderrText) -join [Environment]::NewLine
    $combinedText = Repair-Mojibake $combinedText

    if (-not [string]::IsNullOrWhiteSpace($combinedText)) {
        Add-Content -Path $LogPath -Value $combinedText -Encoding UTF8
    }
    Add-Content -Path $LogPath -Value ("EXIT_CODE={0}" -f $exitCode) -Encoding UTF8

    Remove-Item $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    if ($exitCode -eq 0) {
        Write-Ui "GECTI" ("{0} gecti ({1:N1} sn)." -f $Label, $watch.Elapsed.TotalSeconds)

        # Show stderr as a warning only when the command itself succeeded.
        if (-not [string]::IsNullOrWhiteSpace($stderrText)) {
            Write-Ui "UYARI" ("{0} basarili ancak uyari uretti:" -f $Label)
            Show-TextTail (Repair-Mojibake $stderrText) 5
        }
    }
    else {
        Write-Ui "HATA" ("{0} basarisiz oldu, gercek exit code {1} ({2:N1} sn)." -f $Label, $exitCode, $watch.Elapsed.TotalSeconds)
        Show-TextTail $combinedText 15
    }

    return [int]$exitCode
}


function Invoke-FullVerification {
    param([string]$VerificationPath)

    if (Test-Path $VerificationPath) {
        Remove-Item $VerificationPath -Force
    }

    $results = @()
    $results += Invoke-VerificationCommand `
        -Label "Backend pytest" `
        -WorkingDirectory $ProjectPath `
        -CommandLine "uv run pytest" `
        -LogPath $VerificationPath

    $results += Invoke-VerificationCommand `
        -Label "Backend compileall" `
        -WorkingDirectory $ProjectPath `
        -CommandLine "uv run python -m compileall backend" `
        -LogPath $VerificationPath

    $frontendPath = Join-Path $ProjectPath "frontend"

    $results += Invoke-VerificationCommand `
        -Label "Frontend testleri" `
        -WorkingDirectory $frontendPath `
        -CommandLine "npm test" `
        -LogPath $VerificationPath

    $results += Invoke-VerificationCommand `
        -Label "Frontend lint" `
        -WorkingDirectory $frontendPath `
        -CommandLine "npm run lint" `
        -LogPath $VerificationPath

    $results += Invoke-VerificationCommand `
        -Label "Frontend build" `
        -WorkingDirectory $frontendPath `
        -CommandLine "npm run build" `
        -LogPath $VerificationPath

    return @($results)
}


function Assert-CommandExists {
    param(
        [string]$Name,
        [string]$InstallHint
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw ("'{0}' bulunamadi. {1}" -f $Name, $InstallHint)
    }
}

if (-not (Test-Path $ProjectPath)) {
    throw "Proje klasoru bulunamadi: $ProjectPath"
}

Set-Location $ProjectPath

if (-not (Test-Path ".git")) {
    throw "Bu klasor bir Git repository degil."
}

$currentBranch = (git branch --show-current).Trim()
if ([string]::IsNullOrWhiteSpace($currentBranch)) {
    throw "Aktif Git branch belirlenemedi."
}
if ($currentBranch -in @("master", "main")) {
    throw "Guvenlik nedeniyle '$currentBranch' branch'inde otonom calisma reddedildi."
}

Assert-CommandExists "codex" "npm install -g @openai/codex"
Assert-CommandExists "uv" "uv kurulumunu kontrol et."
Assert-CommandExists "npm" "Node.js/npm kurulumunu kontrol et."

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
        throw "Gerekli autopilot dosyasi eksik: $required"
    }
}

# Codex requires the output schema to be strict JSON without a UTF-8 BOM.
# Windows PowerShell 5.1 commonly writes BOM-prefixed UTF-8 files, so
# normalize and validate the schema before every run.
try {
    $schemaText = [System.IO.File]::ReadAllText(
        $schemaPath,
        [System.Text.Encoding]::UTF8
    )
    $null = $schemaText | ConvertFrom-Json -ErrorAction Stop

    [System.IO.File]::WriteAllText(
        $schemaPath,
        $schemaText,
        (New-Object System.Text.UTF8Encoding($false))
    )
}
catch {
    throw ("cycle-output.schema.json gecersiz JSON: {0}" -f $_.Exception.Message)
}

# Refuse to feed a visibly corrupted roadmap to Codex.
$promptProbe = Get-Content -Raw -Encoding UTF8 $masterPrompt
if ($promptProbe -match "[ÔÇ├┼─]") {
    throw "AUTONOMOUS_AGENT_PROMPT.md karakter kodlamasi bozuk. V3 paketindeki temiz dosyayla degistir."
}

if (-not (Test-Path $statePath)) {
    @(
        "# Autonomous State",
        "",
        "- Current phase: Phase 0",
        "- Status: Resume provider parsing and multilingual quality work.",
        "- Last verified tests: Not yet recorded.",
        "- Next priority: Inspect current implementation and finish Phase 0."
    ) | Set-Content $statePath -Encoding UTF8
}

if (-not (Test-Path $backlogPath)) {
    @(
        "# Backlog",
        "",
        "1. Finish provider parsing and multilingual quality implementation.",
        "2. Add missing regression tests.",
        "3. Run all backend/frontend checks and commit Phase 0.",
        "4. Continue through the gated roadmap."
    ) | Set-Content $backlogPath -Encoding UTF8
}

if (-not (Test-Path $reportPath)) {
    @("# Autonomous Report", "") | Set-Content $reportPath -Encoding UTF8
}

if (-not (Test-Path $issuesPath)) {
    @("# Known Issues", "") | Set-Content $issuesPath -Encoding UTF8
}

# Keep Windows awake while this process is alive.
$awakeType = @(
    "using System;",
    "using System.Runtime.InteropServices;",
    "public static class AwakeStateV3 {",
    "    [DllImport(""kernel32.dll"", SetLastError = true)]",
    "    public static extern uint SetThreadExecutionState(uint esFlags);",
    "}"
) -join [Environment]::NewLine

try {
    Add-Type -TypeDefinition $awakeType -ErrorAction Stop
}
catch {
    if (-not ("AwakeStateV3" -as [type])) {
        throw
    }
}

$ES_CONTINUOUS = [uint32]2147483648
$ES_SYSTEM_REQUIRED = [uint32]1
$ES_AWAYMODE_REQUIRED = [uint32]64
$awakeFlags = [uint32]($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_AWAYMODE_REQUIRED)
[void][AwakeStateV3]::SetThreadExecutionState($awakeFlags)

try {
    Write-Ui "BASLADI" ("Codex Autopilot V3.3 basladi. Branch: {0}" -f $currentBranch)
    Write-Ui "BILGI" ("Durdurmak icin Ctrl+C veya {0} dosyasini olustur." -f $stopPath)

    # npm's codex.ps1 wrapper may write a successful status message to stderr.
    # Windows PowerShell 5.1 can convert that normal stderr text into a
    # NativeCommandError when ErrorActionPreference is Stop. Run the check
    # through cmd.exe and decide only from the native exit code.
    $codexCmd = Get-Command codex.cmd -ErrorAction SilentlyContinue
    if ($codexCmd) {
        $codexCommandPath = $codexCmd.Source
    }
    else {
        $codexCommandPath = (Get-Command codex -ErrorAction Stop).Source
    }

    $loginStatusPath = Join-Path $runDir "login-status.tmp"
    $quotedCodexPath = '"' + $codexCommandPath + '"'
    $quotedStatusPath = '"' + $loginStatusPath + '"'
    $loginCommandLine = $quotedCodexPath + ' login status > ' + $quotedStatusPath + ' 2>&1'

    & cmd.exe /d /s /c $loginCommandLine
    $loginExitCode = $LASTEXITCODE

    if (Test-Path $loginStatusPath) {
        $loginOutputText = Get-Content -Raw -Encoding UTF8 $loginStatusPath
        Remove-Item $loginStatusPath -Force -ErrorAction SilentlyContinue
    }
    else {
        $loginOutputText = ""
    }

    if ($loginExitCode -ne 0) {
        throw ("Codex giris durumu dogrulanamadi: {0}" -f (Repair-Mojibake $loginOutputText).Trim())
    }

    if ([string]::IsNullOrWhiteSpace($loginOutputText)) {
        Write-Ui "GECTI" "Codex giris durumu dogrulandi."
    }
    else {
        Write-Ui "GECTI" (Repair-Mojibake $loginOutputText.Trim())
    }

    if ($VerifyOnly) {
        $verifyPath = Join-Path $runDir "manual-verification.log"
        Write-Ui "TEST" "VerifyOnly modu: gercek backend ve frontend testleri calistirilacak."
        $verification = Invoke-FullVerification $verifyPath
        Copy-Item $verifyPath (Join-Path $runDir "last-verification.log") -Force
        $allPassed = -not ($verification | Where-Object { $_ -ne 0 })

        if ($allPassed) {
            Write-Ui "GECTI" "Tum test komutlari gercekten calisti ve basarili oldu."
            exit 0
        }

        Write-Ui "HATA" ("En az bir test basarisiz. Ayrinti: {0}" -f $verifyPath)
        exit 1
    }

    $iteration = 0
    $consecutiveFailures = 0

    while ($true) {
        if (Test-Path $stopPath) {
            Write-Ui "BILGI" "STOP dosyasi algilandi. Guvenli bicimde cikiliyor."
            break
        }

        while (Test-Path $pausePath) {
            Write-Ui "BEKLE" "PAUSE dosyasi mevcut. 60 saniye bekleniyor."
            Start-Sleep -Seconds 60
            if (Test-Path $stopPath) {
                break
            }
        }

        if (Test-Path $stopPath) {
            break
        }

        $iteration++
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $cycleDir = Join-Path $logDir ("cycle-{0:D4}-{1}" -f $iteration, $stamp)
        New-Item -ItemType Directory -Force $cycleDir | Out-Null

        $promptPath = Join-Path $cycleDir "prompt.md"
        $jsonlPath = Join-Path $cycleDir "events.jsonl"
        $stderrPath = Join-Path $cycleDir "stderr.log"
        $finalPath = Join-Path $cycleDir "final.json"
        $verificationPath = Join-Path $cycleDir "verification.log"

        $templateText = Get-Content -Raw -Encoding UTF8 $cycleTemplate
        $cyclePrompt = $templateText.Replace("{{ITERATION}}", [string]$iteration)
        $cyclePrompt = $cyclePrompt.Replace("{{PROJECT_PATH}}", $ProjectPath)
        $cyclePrompt = $cyclePrompt.Replace("{{TIMESTAMP}}", (Get-Date -Format o))
        $cyclePrompt | Set-Content $promptPath -Encoding UTF8

        $headBefore = (git rev-parse HEAD).Trim()
        Write-Ui "BASLADI" ("Dongu {0} basladi. HEAD={1}" -f $iteration, $headBefore)

        try {
            $promptText = Get-Content -Raw -Encoding UTF8 $promptPath

            $promptText |
                & $codexCommandPath exec `
                    --sandbox workspace-write `
                    --json `
                    --output-schema $schemaPath `
                    --output-last-message $finalPath `
                    - 2> $stderrPath |
                ForEach-Object {
                    $rawLine = [string]$_
                    Add-Content -Path $jsonlPath -Value $rawLine -Encoding UTF8
                    Show-CodexEvent $rawLine
                }

            $codexExit = $LASTEXITCODE
            if ($null -eq $codexExit) {
                $codexExit = 0
            }
        }
        catch {
            $codexExit = 1
            Add-Content -Path $stderrPath -Value ($_.Exception.Message) -Encoding UTF8
        }

        if ($codexExit -eq 0) {
            Write-Ui "GECTI" ("Codex dongusu {0} tamamlandi." -f $iteration)
        }
        else {
            Write-Ui "HATA" ("Codex dongusu {0}, exit code {1} ile bitti." -f $iteration, $codexExit)
            if (Test-Path $stderrPath) {
                $stderrText = Get-Content -Raw $stderrPath
                Show-TextTail $stderrText 20
            }
        }

        Write-Ui "TEST" "Codex'ten bagimsiz tam dogrulama baslatiliyor."
        $verification = Invoke-FullVerification $verificationPath
        Copy-Item $verificationPath (Join-Path $runDir "last-verification.log") -Force

        $allVerificationPassed = -not ($verification | Where-Object { $_ -ne 0 })
        $headAfter = (git rev-parse HEAD).Trim()

        $cycleStatus = "failed"
        $recommendedSleep = $DefaultSleepSeconds
        $finalSummary = $null

        if (Test-Path $finalPath) {
            try {
                $finalObject = Get-Content -Raw -Encoding UTF8 $finalPath | ConvertFrom-Json
                $cycleStatus = [string]$finalObject.cycle_status
                $finalSummary = Repair-Mojibake ([string]$finalObject.summary)
                if ($finalObject.recommended_sleep_seconds) {
                    $recommendedSleep = [int]$finalObject.recommended_sleep_seconds
                }
            }
            catch {
                Write-Ui "UYARI" "Codex final.json raporu okunamadi; loglar sonraki donguye birakildi."
            }
        }

        if ($finalSummary) {
            Write-Ui "CODEX" ("Dongu ozeti: {0}" -f $finalSummary)
        }

        $cycleLeaf = Split-Path $cycleDir -Leaf
        $reportLines = @(
            "",
            ("## Supervisor cycle {0} - {1}" -f $iteration, (Get-Date -Format o)),
            "",
            ("- Codex exit code: {0}" -f $codexExit),
            ("- HEAD before: {0}" -f $headBefore),
            ("- HEAD after: {0}" -f $headAfter),
            ("- Full verification passed: {0}" -f $allVerificationPassed),
            ("- Final report: codex-run/logs/{0}/final.json" -f $cycleLeaf),
            ("- Verification log: codex-run/logs/{0}/verification.log" -f $cycleLeaf),
            ""
        )
        Add-Content -Path $reportPath -Value ($reportLines -join [Environment]::NewLine) -Encoding UTF8

        if ($AutoPush -and $allVerificationPassed -and ($headAfter -ne $headBefore)) {
            Write-Ui "BILGI" "Test edilmis yeni commit bulundu; feature branch push ediliyor."
            $pushOutput = & git push origin HEAD 2>&1
            if ($LASTEXITCODE -eq 0) {
                Write-Ui "GECTI" "Git push basarili."
            }
            else {
                Write-Ui "HATA" "Git push basarisiz; calisma yerelde korunuyor."
                Show-TextTail (($pushOutput | Out-String)) 15
            }
        }

        if ($Once) {
            Write-Ui "BILGI" "Once modu: tek dongu tamamlandi, cikiliyor."
            if (($codexExit -eq 0) -and $allVerificationPassed) {
                exit 0
            }
            exit 1
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

        Write-Ui "BEKLE" ("Dongu bitti. Durum={0}, testler={1}. {2} saniye sonra yeni dongu." -f $cycleStatus, $allVerificationPassed, $sleepSeconds)
        Start-Sleep -Seconds $sleepSeconds
    }
}
finally {
    [void][AwakeStateV3]::SetThreadExecutionState($ES_CONTINUOUS)
    Write-Ui "BILGI" "Codex Autopilot durdu."
}
