#requires -Version 5.1
<#
.SYNOPSIS
    OpenBiliClaw one-command installer for native Windows (PowerShell).

.DESCRIPTION
    Mirrors scripts/install.sh for users on native Windows who do NOT
    want to use Docker or WSL2. Clones the repo, installs Python deps
    via uv (preferred) or pip+venv, runs the bootstrap helper, and
    prints the same status block install.sh emits so an AI coding agent
    (Claude Code, Codex, Cursor, OpenClaw, etc.) can drive the rest of
    the install with no shell-style ambiguity.

.PARAMETER InstallDir
    Target directory. Default: $env:USERPROFILE\OpenBiliClaw

.PARAMETER ReuseFrom
    Path to an existing OpenBiliClaw checkout whose API keys + Bilibili
    cookie should be reused. When unset, the script auto-detects under
    common locations. Pass an empty string to disable auto-detect.

.PARAMETER Branch
    Git branch to clone. Default: main

.PARAMETER Port
    Backend API port. Default: 8420

.PARAMETER ApiHost
    Backend bind address. Default: 0.0.0.0

.PARAMETER Mode
    Bootstrap mode. Default: "local" (no Docker on Windows by design;
    pass --mode docker only if Docker Desktop is configured).

.PARAMETER SkipStart
    When present, agent_bootstrap.py prepares the install but does not
    start the backend. Useful for CI pre-bake.

.EXAMPLE
    # PowerShell 5.1 (Win10/Win11 default) needs the TLS 1.2 prefix —
    # GitHub no longer accepts TLS 1.0/1.1, which is what PS 5.1 picks.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex

.EXAMPLE
    # PowerShell 7+ — TLS 1.2 is already the default, no prefix needed.
    iwr https://raw.githubusercontent.com/whiteguo233/OpenBiliClaw/main/scripts/install.ps1 -UseBasicParsing | iex

.EXAMPLE
    $env:INSTALL_DIR = "$env:USERPROFILE\obc"
    iwr <url> -UseBasicParsing | iex
#>

[CmdletBinding()]
param(
    [string] $InstallDir = $env:INSTALL_DIR,
    [string] $ReuseFrom  = $env:REUSE_FROM,
    [string] $Branch     = $env:OPENBILICLAW_BRANCH,
    [int]    $Port       = 0,
    [string] $ApiHost    = $env:HOST,
    [string] $Mode       = $env:MODE,
    [switch] $SkipStart
)

$ErrorActionPreference = 'Stop'

# Force TLS 1.2 for any HTTP calls. PowerShell 5.1 (the default on
# Windows 10/11 without manual upgrade) defaults to TLS 1.0/1.1 + SSL3,
# but GitHub.com / pypi.org / raw.githubusercontent.com require TLS 1.2+.
# Without this, Invoke-WebRequest / git-https handshakes fail with
# misleading messages like "underlying connection was closed".
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Older .NET (pre-4.5) won't have Tls12 — nothing we can do, but most
    # PS 5.1 installs ship .NET 4.6+ so this almost never triggers.
}

function Add-LocalNoProxy {
    $parts = @()
    foreach ($name in @('NO_PROXY', 'no_proxy')) {
        $raw = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($raw) {
            foreach ($part in ($raw -split ',')) {
                $value = $part.Trim()
                if ($value -and -not $parts.Contains($value)) {
                    $parts += $value
                }
            }
        }
    }
    foreach ($hostName in @('localhost', '127.0.0.1', '::1')) {
        if (-not $parts.Contains($hostName)) {
            $parts += $hostName
        }
    }
    $value = ($parts -join ',')
    $env:NO_PROXY = $value
    $env:no_proxy = $value
}

Add-LocalNoProxy

# -----------------------------------------------------------------------------
# Defaults

$DefaultRepoUrl    = 'https://github.com/whiteguo233/OpenBiliClaw.git'
$DefaultBranch     = 'main'
$DefaultInstallDir = Join-Path $env:USERPROFILE 'OpenBiliClaw'
$CandidateSources  = @(
    Join-Path $env:USERPROFILE 'workspace\OpenBiliClaw'
    Join-Path $env:USERPROFILE 'OpenBiliClaw'
    Join-Path $env:USERPROFILE 'projects\OpenBiliClaw'
    Join-Path $env:USERPROFILE 'code\OpenBiliClaw'
)

if (-not $InstallDir) { $InstallDir = $DefaultInstallDir }
if (-not $Branch)     { $Branch     = if ($env:OPENBILICLAW_BRANCH) { $env:OPENBILICLAW_BRANCH } else { $DefaultBranch } }
if ($Port -le 0)      { $Port       = if ($env:PORT) { [int]$env:PORT } else { 8420 } }
if (-not $ApiHost)    { $ApiHost    = '0.0.0.0' }
if (-not $Mode)       { $Mode       = 'local' }   # native Windows defaults to local, not docker
$RepoUrl = if ($env:OPENBILICLAW_REPO_URL) { $env:OPENBILICLAW_REPO_URL } else { $DefaultRepoUrl }

# Distinguish "user explicitly set ReuseFrom='' to disable" vs "not passed".
$ReuseExplicit = $PSBoundParameters.ContainsKey('ReuseFrom') -or ($null -ne $env:REUSE_FROM)

# -----------------------------------------------------------------------------
# Logging helpers

function Write-LogLine([string]$Color, [string]$Message) {
    Write-Host -NoNewline -ForegroundColor $Color '[openbiliclaw] '
    Write-Host $Message
}
function Log-Info  { param($m) Write-LogLine 'Cyan'   $m }
function Log-OK    { param($m) Write-LogLine 'Green'  $m }
function Log-Warn  { param($m) Write-LogLine 'Yellow' $m }
function Log-Err   { param($m) Write-LogLine 'Red'    $m }

# -----------------------------------------------------------------------------
# Prerequisites

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Log-Err "Missing required command: $Name"
        if ($Hint) { Log-Err "  $Hint" }
        exit 1
    }
}

function Get-PythonExe {
    foreach ($candidate in @('python', 'python3', 'py')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            try {
                if ($candidate -eq 'py') {
                    $version = & $cmd -3.11 -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                    if (-not $version) {
                        $version = & $cmd -3 -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                    }
                } else {
                    $version = & $cmd -c 'import sys; print(sys.version_info[0], sys.version_info[1])' 2>$null
                }
            } catch {
                continue
            }
            if (-not $version) { continue }
            # Python prints 'major minor' (whitespace-separated) — using
            # print(major, minor) instead of an f-string avoids a PS 5.1
            # quoting bug where inner double-quotes / { } get stripped
            # before reaching python.exe, which would yield SyntaxError
            # on the Python side and falsely trigger "Python 3.11+ is
            # required."
            $parts = $version.Trim() -split '\s+'
            if ($parts.Count -lt 2) { continue }
            $major = [int]$parts[0]; $minor = [int]$parts[1]
            if (($major -gt 3) -or ($major -eq 3 -and $minor -ge 11)) {
                return $cmd.Path
            }
        }
    }
    Log-Err 'Python 3.11+ is required.'
    Log-Err '  Install from https://www.python.org/downloads/  (check "Add python.exe to PATH" during install)'
    exit 1
}

# -----------------------------------------------------------------------------
# Source discovery (auto-reuse existing install)

function Detect-ReuseSource {
    if ($ReuseExplicit) {
        if ($ReuseFrom) { Log-Info "REUSE_FROM explicitly set to $ReuseFrom" }
        else            { Log-Info 'REUSE_FROM explicitly set to empty — skipping auto-detection.' }
        return
    }
    foreach ($cand in $CandidateSources) {
        if ($cand -ieq $InstallDir) { continue }
        if (-not (Test-Path $cand -PathType Container)) { continue }
        $hasConfig = Test-Path (Join-Path $cand 'config.toml')
        $hasCookie = Test-Path (Join-Path $cand 'data\bilibili_cookie.json')
        if ($hasConfig -or $hasCookie) {
            $script:ReuseFrom = $cand
            Log-Info "Found existing OpenBiliClaw at $ReuseFrom — will reuse API keys and cookie."
            return
        }
    }
}

# -----------------------------------------------------------------------------
# Checkout: clone or update existing

function Ensure-Checkout {
    $hasPyproject = Test-Path (Join-Path $InstallDir 'pyproject.toml')
    $hasExample   = Test-Path (Join-Path $InstallDir 'config.example.toml')

    if ($hasPyproject -and $hasExample) {
        Log-Info "Using existing checkout at $InstallDir"
        # Auto-update when safe: clean working tree + fast-forward available.
        if (Test-Path (Join-Path $InstallDir '.git')) {
            try {
                Push-Location $InstallDir
                git fetch --quiet origin $Branch 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    Log-Warn 'git fetch failed; skipping update check.'
                    return
                }
                $local  = (git rev-parse HEAD 2>$null).Trim()
                $remote = (git rev-parse "origin/$Branch" 2>$null).Trim()
                if (-not $local -or -not $remote -or $local -eq $remote) { return }
                $behind = (git rev-list --count "$local..$remote" 2>$null).Trim()
                $dirty  = git status --porcelain 2>$null
                if ($dirty) {
                    Log-Warn "⚠ Existing checkout is $behind commits behind origin/$Branch but has local changes — skipping auto-update."
                    Log-Warn "  Manual update: cd $InstallDir; git stash; git pull; git stash pop"
                    return
                }
                Log-Info "Updating existing checkout: $behind commits behind origin/$Branch — pulling…"
                git pull --ff-only --quiet origin $Branch
                if ($LASTEXITCODE -eq 0) {
                    $sha = (git rev-parse --short HEAD).Trim()
                    Log-OK "✓ Updated to $sha"
                } else {
                    Log-Warn 'git pull failed (non-fast-forward?); keeping current checkout.'
                    Log-Warn "  Force fresh install: Remove-Item -Recurse -Force $InstallDir ; rerun this installer"
                }
            } finally {
                Pop-Location
            }
        }
        return
    }

    if ((Test-Path $InstallDir) -and ((Get-ChildItem -Path $InstallDir -Force | Measure-Object).Count -gt 0)) {
        Log-Err "Target directory is not empty and not an OpenBiliClaw checkout: $InstallDir"
        Log-Err 'Set $env:INSTALL_DIR to an empty/non-existent path, or remove the existing one first.'
        exit 1
    }

    $parent = Split-Path $InstallDir -Parent
    if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Log-Info "Cloning $RepoUrl (branch $Branch) into $InstallDir"
    git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Log-Err 'git clone failed.'; exit 1 }
}

# -----------------------------------------------------------------------------
# Run bootstrap

$script:BootstrapLog = ''
function Cleanup-BootstrapLog {
    if ($script:BootstrapLog -and (Test-Path $script:BootstrapLog)) {
        Remove-Item -Force $script:BootstrapLog -ErrorAction SilentlyContinue
    }
}

function Invoke-Bootstrap([string]$PythonExe) {
    $bootstrap = Join-Path $InstallDir 'scripts\agent_bootstrap.py'
    if (-not (Test-Path $bootstrap)) {
        Log-Err "Bootstrap script missing: $bootstrap"
        Log-Err "Your checkout may be stale — cd $InstallDir; git pull and retry."
        exit 1
    }
    $args = @(
        '--project-dir', $InstallDir
        '--mode',        $Mode
        '--host',        $ApiHost
        '--port',        "$Port"
    )
    if ($ReuseFrom) { $args += '--reuse-from'; $args += $ReuseFrom }
    if ($SkipStart) { $args += '--skip-start' }
    if (-not $env:OPENBILICLAW_NONINTERACTIVE -and -not $env:CI) {
        $args += '--interactive-confirm'
        $args += '--wait-for-extension-cookie'
    }

    $script:BootstrapLog = [IO.Path]::GetTempFileName()
    Log-Info "Running bootstrap: $PythonExe $bootstrap $($args -join ' ')"

    & $PythonExe $bootstrap @args 2>&1 | Tee-Object -FilePath $script:BootstrapLog
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Log-Err "Bootstrap exited with code $rc."
        Log-Err "The log above contains [bootstrap] lines and BOOTSTRAP_STATUS JSON events; the 'error' event tells you which step failed."
        Log-Err 'Once the underlying issue is fixed, re-run this installer.'
        exit $rc
    }
}

# -----------------------------------------------------------------------------
# Status block (parsed from agent_bootstrap.py's BOOTSTRAP_STATUS lines)

function Print-InstallSummary([string]$PythonExe) {
    $parser = @'
import json
import sys
from pathlib import Path
log_path, install_dir, port, host, reuse_from = sys.argv[1:6]
final = None
for raw in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
    marker = "BOOTSTRAP_STATUS:"
    if marker in raw:
        try:
            final = json.loads(raw.split(marker, 1)[1].strip())
        except json.JSONDecodeError:
            pass
if final is None:
    print("STATUS=unknown")
    print("HEALTH_URL=")
    print("MISSING=")
    sys.exit(0)
details = final.get("details") or {}
missing = details.get("missing") or []
init_decisions = details.get("init_decisions") or {}
decision_missing = init_decisions.get("missing") or []
xhs_flag = ((init_decisions.get("xhs") or {}).get("flag") or "")
douyin_flag = ((init_decisions.get("douyin") or {}).get("flag") or "")
youtube_flag = ((init_decisions.get("youtube") or {}).get("flag") or "")
print(f"STATUS={final.get('status', 'unknown')}")
print(f"HEALTH_URL={details.get('health_url', '')}")
print(f"MISSING={','.join(missing)}")
print(f"DECISIONS={','.join(decision_missing)}")
print(f"XHS_FLAG={xhs_flag}")
print(f"DOUYIN_FLAG={douyin_flag}")
print(f"YOUTUBE_FLAG={youtube_flag}")
'@
    # PS 5.1 (Windows 10/11 default) lacks the ?? null-coalescing operator
    # — that's a PS 7+ feature. Use a defensive fallback instead.
    $reuseArg = if ($null -ne $ReuseFrom) { $ReuseFrom } else { '' }
    $summary = & $PythonExe -c $parser $script:BootstrapLog $InstallDir "$Port" $ApiHost $reuseArg

    $status = ''; $healthUrl = ''; $missing = ''; $decisions = ''; $xhsFlag = ''; $douyinFlag = ''; $youtubeFlag = ''
    foreach ($line in $summary -split "`r?`n") {
        if ($line -like 'STATUS=*')     { $status    = $line.Substring(7) }
        elseif ($line -like 'HEALTH_URL=*') { $healthUrl = $line.Substring(11) }
        elseif ($line -like 'MISSING=*')    { $missing   = $line.Substring(8) }
        elseif ($line -like 'DECISIONS=*')  { $decisions = $line.Substring(10) }
        elseif ($line -like 'XHS_FLAG=*')   { $xhsFlag   = $line.Substring(9) }
        elseif ($line -like 'DOUYIN_FLAG=*') { $douyinFlag = $line.Substring(12) }
        elseif ($line -like 'YOUTUBE_FLAG=*') { $youtubeFlag = $line.Substring(13) }
    }
    if (-not $xhsFlag) { $xhsFlag = '--no-xhs' }
    if (-not $douyinFlag) { $douyinFlag = '--no-douyin' }
    if (-not $youtubeFlag) { $youtubeFlag = '--no-youtube' }
    if (-not $healthUrl) {
        if ($ApiHost -in @('0.0.0.0', '::', '[::]')) {
            $healthUrl = "http://127.0.0.1:$Port/api/health"
        } else {
            $healthUrl = "http://${ApiHost}:$Port/api/health"
        }
    }

    # v0.3.20: distinguish "only B站 cookie missing" (the expected state for
    # users on the recommended browser-extension auto-sync path) from
    # "still need an LLM key". The yellow + "partial / credentials still
    # missing" wording on the cookie-only case used to read like an
    # install failure to users.
    $missingOnlyCookie = ($missing -eq 'bilibili.cookie')

    Write-Host ''
    Write-Host '================================================================'
    Write-Host ' OpenBiliClaw ' -NoNewline
    if ($status -eq 'complete') {
        Write-Host 'install complete' -ForegroundColor Green
    } elseif ($status -eq 'needs_decisions') {
        Write-Host 'backend ready - waiting for init choices' -ForegroundColor Green
    } elseif ($missingOnlyCookie) {
        Write-Host 'backend ready - waiting for browser extension to sync B站 Cookie' -ForegroundColor Green
    } elseif ($missing) {
        Write-Host 'install partial (credentials still missing)' -ForegroundColor Yellow
    } else {
        Write-Host "install status: $status" -ForegroundColor Yellow
    }
    Write-Host '================================================================'
    Write-Host "Status:      $status"
    Write-Host "Checkout:    $InstallDir"
    if ($ReuseFrom) {
        Write-Host "Reused from: $ReuseFrom"
        Write-Host '             [i] Reused API keys / B站 cookie are NOT validated.'
        Write-Host '                 B站 cookies expire within weeks; if the previous'
        Write-Host '                 install''s cookie is stale, init will silently'
        Write-Host '                 return 0 history items and the soul profile will'
        Write-Host '                 be hollow. Agents: surface this reuse to the user'
        Write-Host '                 (HARD RULE 4 in docs/agent-install.md) before'
        Write-Host '                 proceeding - do not skip the cookie question just'
        Write-Host '                 because the file already exists.'
        Write-Host '                 To skip auto-reuse and supply fresh credentials:'
        Write-Host '                 install.ps1 -ReuseFrom ""'
    }
    Write-Host "Health URL:  $healthUrl"
    if ($missing) { Write-Host "Missing:     $missing" }
    else { Write-Host 'Missing:     (none)' }
    if ($decisions) { Write-Host "Init choices needed: $decisions" }
    Write-Host ''

    if ($status -eq 'needs_decisions') {
        Write-Host 'Next steps - ask the user before running init:'
        Write-Host ''
        Write-Host '  1. Embedding service (default recommendation):'
        Write-Host '       Local Ollama bge-m3 - free/offline/no extra API key.'
        Write-Host '       If they choose Gemini/OpenAI/custom instead, replace'
        Write-Host '       the --embedding-* flags below.'
        Write-Host ''
        Write-Host '  2. Source bootstrap data (privacy choice):'
        Write-Host '       Ask whether to include Xiaohongshu likes/favorites and'
        Write-Host '       Douyin post/favorite/like/follow and YouTube history/'
        Write-Host '       subscriptions/likes in the initial profile.'
        Write-Host '       Default is NO unless they opt in per source.'
        Write-Host ''
        Write-Host '  3. Re-run bootstrap with explicit choices (DO NOT add --skip-init):'
        Write-Host ''
        Write-Host "     python $InstallDir\scripts\agent_bootstrap.py ``"
        Write-Host "         --project-dir $InstallDir ``"
        if ($decisions -match 'embedding') {
            Write-Host "         --embedding-provider ollama ``"
            Write-Host "         --embedding-model bge-m3 ``"
        }
        Write-Host "         $xhsFlag ``"
        Write-Host "         $douyinFlag ``"
        Write-Host "         $youtubeFlag ``"
        Write-Host "         --port $Port --host $ApiHost"
        Write-Host ''
        Write-Host '     Use --yes-xhs / --yes-douyin / --yes-youtube only after'
        Write-Host '     the user says yes; otherwise keep the matching --no-* flag.'
        Write-Host '     This then runs init: B站 history, soul profile, first discovery.'
    } elseif ($missingOnlyCookie) {
        Write-Host 'Next step - get your B站 Cookie to the backend (pick ONE):'
        Write-Host ''
        Write-Host '  (A) [recommended, zero config]'
        Write-Host '      Install the browser extension and log in to bilibili.com.'
        Write-Host '      It auto-syncs your cookie to this backend within seconds.'
        Write-Host '        Extension: https://github.com/whiteguo233/OpenBiliClaw/releases'
        Write-Host '      Once the cookie arrives, ask the init choices below and'
        Write-Host "      re-run bootstrap so it can run 'openbiliclaw init'."
        Write-Host ''
        Write-Host '      Required before init:'
        Write-Host '        - Embedding model/service (default: Ollama bge-m3)'
        Write-Host '        - Xiaohongshu likes/favorites? (default: no; yes only on opt-in)'
        Write-Host '        - Douyin post/favorite/like/follow? (default: no; yes only on opt-in)'
        Write-Host '        - YouTube history/subscriptions/likes? (default: no; yes only on opt-in)'
        Write-Host ''
        Write-Host '  (B) [manual fallback]'
        Write-Host "      F12 -> Network -> copy the 'Cookie' header from any"
        Write-Host '      bilibili.com request, then run:'
        Write-Host "        python $InstallDir\scripts\agent_bootstrap.py ``"
        Write-Host "            --project-dir $InstallDir ``"
        Write-Host "            --bilibili-cookie '<YOUR_COOKIE>' ``"
        if ($decisions -match 'embedding') {
            Write-Host "            --embedding-provider ollama ``"
            Write-Host "            --embedding-model bge-m3 ``"
        }
        Write-Host "            $xhsFlag ``"
        Write-Host "            $douyinFlag ``"
        Write-Host "            $youtubeFlag ``"
        Write-Host "            --port $Port --host $ApiHost"
        Write-Host '      Use --yes-xhs / --yes-douyin / --yes-youtube only after'
        Write-Host '      the user opts in; otherwise keep the matching --no-* flag.'
        Write-Host ''
        Write-Host '  Verify the backend is healthy any time:'
        Write-Host "      Invoke-RestMethod $healthUrl"
    } elseif ($missing) {
        Write-Host 'Next steps (credentials are missing):'
        Write-Host ''
        Write-Host '  1. Choose your LLM provider (default: deepseek):'
        Write-Host '     Supported: deepseek | openai | gemini | claude | openrouter | ollama'
        Write-Host ''
        if ($missing -match 'api_key') {
            Write-Host '     LLM API key - get one from your chosen provider:'
            Write-Host '         DeepSeek:   https://platform.deepseek.com/api_keys'
            Write-Host '         OpenAI:     https://platform.openai.com/api-keys'
            Write-Host '         Gemini:     https://aistudio.google.com/apikey'
            Write-Host '         Claude:     https://console.anthropic.com/settings/keys'
            Write-Host '         OpenRouter: https://openrouter.ai/keys'
            Write-Host '         Ollama:     (no key needed, just install and run)'
            Write-Host ''
        }
        if ($missing -match 'bilibili.cookie') {
            Write-Host '     For the Bilibili cookie you have TWO options (pick ONE):'
            Write-Host ''
            Write-Host '     (A) [recommended] Install the browser extension and let it'
            Write-Host '         auto-sync - no F12, no copy/paste.'
            Write-Host '         Download: https://github.com/whiteguo233/OpenBiliClaw/releases'
            Write-Host '         Log in to bilibili.com if you are not already; the extension'
            Write-Host '         pushes the cookie to this backend within seconds. You can then'
            Write-Host '         omit --bilibili-cookie below after the extension syncs.'
            Write-Host ''
            Write-Host '     (B) Paste the cookie manually via --bilibili-cookie below.'
            Write-Host ''
        }
        Write-Host '  2. Ask which embedding service to use:'
        Write-Host '     Default: local Ollama bge-m3 (free/offline/no extra API key).'
        Write-Host '     Alternatives: Gemini embedding, OpenAI text-embedding-3-small,'
        Write-Host '     or a custom OpenAI-compatible embedding endpoint.'
        Write-Host ''
        Write-Host '  3. Ask whether to include source bootstrap data:'
        Write-Host '     Xiaohongshu likes/favorites, Douyin post/favorite/like/follow,'
        Write-Host '     and YouTube history/subscriptions/likes.'
        Write-Host '     Default: no. Use --yes-* flags only after explicit opt-in.'
        Write-Host ''
        Write-Host '  4. Prepare missing values, then run with values filled in (DO NOT add --skip-init):'
        Write-Host ''
        Write-Host "     python $InstallDir\scripts\agent_bootstrap.py ``"
        Write-Host "         --project-dir $InstallDir ``"
        Write-Host "         --provider <YOUR_PROVIDER> ``"
        if ($missing -match 'api_key')         { Write-Host "         --llm-api-key '<YOUR_API_KEY>' ``" }
        if ($decisions -match 'embedding') {
            Write-Host "         --embedding-provider ollama ``"
            Write-Host "         --embedding-model bge-m3 ``"
        }
        Write-Host "         $xhsFlag ``"
        Write-Host "         $douyinFlag ``"
        Write-Host "         $youtubeFlag ``"
        if ($missing -match 'bilibili.cookie') { Write-Host "         --bilibili-cookie '<YOUR_COOKIE>' ``" }
        Write-Host "         --port $Port --host $ApiHost"
        Write-Host ''
        Write-Host '     Replace the embedding/source flags according to the'
        Write-Host '     user answers before running the command.'
        Write-Host ''
        Write-Host "     This auto-runs 'openbiliclaw init' once credentials check out:"
        Write-Host '       - pulls your Bilibili history'
        Write-Host '       - generates the soul profile'
        Write-Host '       - runs the first content discovery pass'
        Write-Host '     Takes 2-5 minutes. Without this step the extension shows nothing.'
        Write-Host '     During init, relay BOOTSTRAP_STATUS init_progress events to the user.'
        Write-Host ''
        Write-Host '  3. Verify the backend is healthy:'
        Write-Host "      Invoke-RestMethod $healthUrl"
    } else {
        Write-Host 'All credentials present — init has been run automatically.'
        Write-Host ''
        Write-Host '  - Verify the backend is healthy:'
        Write-Host "      Invoke-RestMethod $healthUrl"
        Write-Host '  - See recommendations:'
        Write-Host "      Set-Location $InstallDir ; uv run openbiliclaw recommend"
        Write-Host '  - View your soul profile:'
        Write-Host "      Set-Location $InstallDir ; uv run openbiliclaw profile"
        Write-Host '  - Re-run init manually if needed:'
        Write-Host "      Set-Location $InstallDir ; uv run openbiliclaw init"
    }
    Write-Host ''
    Write-Host 'Optional: enable local Ollama as the embedding fallback'
    Write-Host '  (no extra API key needed; useful if your remote embedding quota runs out)'
    Write-Host '      1. Install Ollama from https://ollama.com/download'
    Write-Host '      2. Start Ollama (the desktop app launches the local service automatically)'
    Write-Host '      3. Set-Location ' "$InstallDir" ' ; uv run openbiliclaw setup-embedding'
    Write-Host ''
    Write-Host 'Reference docs:'
    Write-Host "  - $InstallDir\docs\agent-install.md     (install guide)"
    Write-Host "  - $InstallDir\docs\agent-deployment.md  (troubleshooting)"
    Write-Host '================================================================'
}

# -----------------------------------------------------------------------------
# Main

function Main {
    Log-Info 'OpenBiliClaw one-command installer (Windows / PowerShell)'
    Require-Command 'git' 'Install Git from https://git-scm.com/downloads'
    $pythonExe = Get-PythonExe
    Detect-ReuseSource
    Ensure-Checkout
    try {
        Invoke-Bootstrap $pythonExe
        Print-InstallSummary $pythonExe
    } finally {
        Cleanup-BootstrapLog
    }
}

try {
    Main
} catch {
    Log-Err $_.Exception.Message
    exit 1
}
