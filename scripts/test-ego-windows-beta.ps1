#requires -Version 5.1
# Test the installed official Ego Lite Windows beta from a desktop PowerShell.
# The report contains only diagnostic codes, flags, counts and version numbers.
# Exit codes: 0 = both passed, 1 = a probe failed, 2 = a prerequisite is missing.
[CmdletBinding()]
param(
    [string]$BrowserExe,
    [string]$CliExe,
    [ValidateRange(30, 240)][int]$TimeoutSeconds = 120,
    [string]$ReportPath
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    Write-Error 'Run this script in Windows PowerShell or PowerShell on a Windows desktop.'
}

$probeModule = @'
import http from 'node:http';

const prefix = 'OBC_EGO_PROBE ';
const rpcTimeoutMs = 10000;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const same = (left, right) => typeof left === 'string' && left.toLowerCase() === right.toLowerCase();

function assert(condition, code) {
  if (!condition) throw new Error(code);
}

function safeCode(error) {
  const candidate = error?.error_code ?? error?.code ?? error?.message;
  if (typeof candidate === 'number') return 'cdp_' + candidate;
  return /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(candidate ?? '') ? candidate : 'unclassified_error';
}

function bounded(operation, ms = rpcTimeoutMs) {
  let timer;
  return Promise.race([
    Promise.resolve().then(operation),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('native_operation_timeout')), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

function native(result) {
  if (result?.error || result?.error_code) {
    throw new Error(result.error_code || 'native_operation_rejected');
  }
  return result;
}

export async function run(config) {
  const ego = globalThis.ego;
  const pending = new Map();
  const counts = { page: 0, blocked: 0 };
  const events = { allowedRequest: false, allowedResponse: false, blockedRequest: false };
  let check = 'native_binding';
  let task;
  let server;
  let origin;
  let session;
  let interceptionError;
  let nextId = 0;

  const emit = (name, status, details = {}) =>
    console.log(prefix + JSON.stringify({ name, status, ...details }));

  async function step(name, operation) {
    check = name;
    const details = await operation();
    emit(name, 'pass', details);
  }

  function send(method, params = {}, sessionId = undefined) {
    const id = ++nextId;
    return bounded(() => new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ego.sendCDPMessage(JSON.stringify({
        id, method, params, ...(sessionId ? { sessionId } : {}),
      }));
    })).finally(() => pending.delete(id));
  }

  async function handleRequest({ sessionId, params }) {
    const { requestId, request, responseStatusCode } = params;
    try {
      if (sessionId !== session || request.method !== 'GET') {
        await send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }, sessionId);
      } else if (request.url === origin + '/' && !responseStatusCode) {
        await send('Fetch.continueRequest', { requestId }, sessionId);
        events.allowedRequest = true;
      } else if (request.url === origin + '/' && responseStatusCode === 200) {
        await send('Fetch.continueResponse', { requestId }, sessionId);
        events.allowedResponse = true;
      } else {
        await send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }, sessionId);
        if (request.url === origin + '/should-block') events.blockedRequest = true;
      }
    } catch (error) {
      interceptionError = safeCode(error);
    }
  }

  try {
    await step('native_binding', () => {
      const methods = [
        'getBrowserVersion', 'listProfiles', 'createTaskSpace', 'useTaskSpace',
        'createTab', 'closeTaskSpace', 'sendCDPMessage',
      ];
      assert(ego && methods.every(method => typeof ego[method] === 'function'), 'native_binding_missing');
    });

    await step('version_and_profile', async () => {
      const version = native(await bounded(() => ego.getBrowserVersion())).currentVersion;
      const { profiles } = native(await bounded(() => ego.listProfiles()));
      assert(/^\d+(?:\.\d+){1,5}$/.test(version), 'browser_version_invalid');
      assert(Array.isArray(profiles) && profiles.some(row => row.id === 'Default'), 'default_profile_unavailable');
      return { version };
    });

    if (config.browserPid) {
      await step('cold_host_identity', () => {
        assert(process.ppid === config.browserPid, 'wrong_named_browser');
        const normalize = value => value.replaceAll('/', '\\');
        assert(same(normalize(process.execPath), normalize(config.browserExe)), 'wrong_browser_image');
      });
    }

    await step('task_space', async () => {
      task = native(await bounded(() => ego.createTaskSpace(config.task, 'Default')));
      assert(Number.isSafeInteger(task?.id), 'task_space_id_invalid');
      ego.useTaskSpace(task.id);
      // Select only the Space just created by this invocation.
      ego.onSendCDPMessageError = (_message, code) => {
        for (const waiter of pending.values()) waiter.reject(new Error(code || 'native_cdp_send_error'));
      };
      ego.onCDPMessage = raw => {
        let message;
        try { message = JSON.parse(raw); } catch { return; }
        if (message.id) {
          const waiter = pending.get(message.id);
          if (!waiter) return;
          if (message.error) waiter.reject({ code: message.error.code });
          else waiter.resolve(message.result);
        } else if (message.method === 'Fetch.requestPaused' && message.sessionId === session) {
          void handleRequest(message);
        }
      };
    });

    await step('tab_and_target', async () => {
      const { targetId } = native(await bounded(() => ego.createTab('about:blank')));
      assert(typeof targetId === 'string' && targetId, 'tab_target_missing');
      const result = await send('Target.attachToTarget', { targetId, flatten: true });
      session = result?.sessionId;
      assert(typeof session === 'string' && session, 'cdp_session_missing');
      await send('Page.enable', {}, session);
      await send('Network.enable', {}, session);
      await send('Network.setBypassServiceWorker', { bypass: true }, session);
      await send('Target.setAutoAttach', {
        autoAttach: true, waitForDebuggerOnStart: true, flatten: true,
      }, session);
    });

    await step('fetch_interception', async () => {
      server = http.createServer((request, response) => {
        if (request.url === '/') {
          counts.page++;
          response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
          response.end('<!doctype html><title>Ego probe</title><h1 id="probe">Ego Windows Beta</h1><img src="/should-block" alt="">');
        } else {
          if (request.url === '/should-block') counts.blocked++;
          response.writeHead(404).end();
        }
      });
      await bounded(() => new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(0, '127.0.0.1', resolve);
      }));
      origin = 'http://127.0.0.1:' + server.address().port;

      await send('Fetch.enable', {
        patterns: [
          { urlPattern: '*', requestStage: 'Request' },
          { urlPattern: '*', requestStage: 'Response' },
        ],
      }, session);
      const nav = await send('Page.navigate', { url: origin + '/' }, session);
      if (nav?.errorText) {
        throw new Error((/^net::(ERR_[A-Z0-9_]{1,60})$/.exec(nav.errorText) || [])[1] || 'fixture_navigation_failed');
      }
      const deadline = Date.now() + 8000;
      while (!events.blockedRequest && !interceptionError && !counts.blocked && Date.now() < deadline) {
        await pause(100);
      }
      assert(!interceptionError, interceptionError);
      assert(counts.blocked === 0, 'blocked_request_reached_server');
      assert(events.allowedRequest && events.allowedResponse && counts.page === 1, 'allow_path_not_verified');
      assert(events.blockedRequest, 'deny_path_not_verified');
      return { allowedPageHits: counts.page, blockedResourceHits: counts.blocked };
    });

    await step('dom_and_screenshot', async () => {
      const result = await send('Runtime.evaluate', {
        expression: "document.querySelector('#probe')?.textContent", returnByValue: true,
      }, session);
      assert(result?.result?.value === 'Ego Windows Beta', 'fixture_dom_not_readable');
      const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false }, session);
      assert(typeof shot?.data === 'string' && shot.data.startsWith('iVBORw0KGgo'), 'screenshot_png_missing');
    });
  } catch (error) {
    emit(check, 'fail', { code: safeCode(error) });
  } finally {
    if (task && Number.isSafeInteger(task.id)) {
      try {
        ego.useTaskSpace(task.id);
        native(await bounded(() => ego.closeTaskSpace(), 5000));
        emit('task_cleanup', 'pass');
      } catch (error) {
        emit('task_cleanup', 'fail', { code: safeCode(error) });
      }
    }
    if (server) {
      server.closeAllConnections?.();
      server.close();
    }
    if (ego && typeof ego.sendCDPMessage === 'function') {
      ego.onCDPMessage = null;
      ego.onSendCDPMessageError = null;
    }
  }
}
'@

function Find-EgoCli {
    if ($CliExe) {
        return (Get-Item -LiteralPath $CliExe).FullName
    }
    $command = Get-Command ego-browser.exe -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Path
    }
    $installed = Join-Path $env:USERPROFILE '.local\share\ego\active_version_dir\ego-browser.exe'
    if (Test-Path -LiteralPath $installed -PathType Leaf) {
        return $installed
    }
}

function Find-EgoBrowser([string]$CliPath) {
    if ($BrowserExe) {
        return (Get-Item -LiteralPath $BrowserExe).FullName
    }
    $cliDirectory = (Get-Item -LiteralPath (Split-Path -Parent $CliPath))
    $junction = $cliDirectory.PSObject.Properties['Target']
    $directories = @(
        $cliDirectory.FullName
        $cliDirectory.Parent.FullName
        $(if ($junction) { $junction.Value })
        (Join-Path $env:LOCALAPPDATA 'Citro Labs\ego\Application')
        (Join-Path $env:LOCALAPPDATA 'Citro Labs\ego lite\Application')
        (Join-Path $env:LOCALAPPDATA 'Ego Lite')
        (Join-Path $env:LOCALAPPDATA 'Ego Lite\Application')
        (Join-Path $env:LOCALAPPDATA 'EgoLite\Application')
        (Join-Path $env:LOCALAPPDATA 'Ego\Application')
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Citro Labs\ego\Application' })
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'Citro Labs\ego lite\Application' })
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'ego\Application' })
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($directory in $directories) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
        $candidate = Get-ChildItem -LiteralPath $directory -File -Filter '*.exe' |
            Where-Object {
                $_.Name -match '^(ego[ ._-]*lite|ego)\.exe$' -or
                ($_.Name -eq 'chrome.exe' -and $_.VersionInfo.ProductName -match 'Ego[ ._-]*Lite')
            } | Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    foreach ($process in (Get-Process -Name 'ego lite', 'egolite', 'ego-lite', 'ego' -ErrorAction SilentlyContinue)) {
        if ($process.Path -and $process.FileVersionInfo.ProductName -match 'Ego[ ._-]*Lite') {
            return $process.Path
        }
    }
}

function Start-ProbeProcess([string]$Executable, [string]$Arguments, [bool]$Capture) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.Arguments = $Arguments
    $info.WorkingDirectory = Split-Path -Parent $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $Capture
    $info.RedirectStandardOutput = $Capture
    $info.RedirectStandardError = $Capture
    return [System.Diagnostics.Process]::Start($info)
}

function Stop-ProbeProcess([System.Diagnostics.Process]$Process, [switch]$OwnedBrowserTree) {
    if ($Process -and -not $Process.HasExited) {
        # The default CLI may bootstrap the user's browser. Only the explicitly
        # launched temporary browser qualifies for terminating the whole tree.
        if ($OwnedBrowserTree) {
            $null = $Process.Handle
            & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $Process.Id /T /F 2>&1 |
                Out-Null
        } else {
            $Process.Kill()
        }
        if (-not $Process.WaitForExit(3000)) {
            $Process.Kill()
            $null = $Process.WaitForExit(3000)
        }
    }
}

function To-Base64([string]$Value) {
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
}

function Invoke-NativeProbe(
    [string]$CliPath, [string]$ModulePath, [hashtable]$Config,
    [System.Diagnostics.Process]$HostProcess
) {
    $report = [ordered]@{
        status = 'fail'
        code = $null
        cliExit = $null
        visibleRootWindowObserved = $null
        windowSamples = 0
        checks = @()
    }
    $cli = $null
    $module64 = To-Base64 $ModulePath
    $config64 = To-Base64 ($Config | ConvertTo-Json -Compress)
    $script = "const u=await import('node:url');const m=await import(u.pathToFileURL(Buffer.from('$module64','base64').toString()).href);await m.run(JSON.parse(Buffer.from('$config64','base64').toString()))"
    $arguments = 'nodejs -e "{0}"' -f $script
    if ($Config.ContainsKey('server')) {
        $arguments = '--ego-server-name={0} {1}' -f $Config.server, $arguments
    }
    try {
        if ($HostProcess) {
            $report.visibleRootWindowObserved = $false
            # Sample from the cold process start, before contacting its native CLI.
            for ($sample = 0; $sample -lt 12; $sample++) {
                if ($HostProcess.HasExited) {
                    $report.code = 'cold_browser_process_exited'
                    return $report
                }
                $report.visibleRootWindowObserved = $report.visibleRootWindowObserved -or
                    [EgoWindowsBetaProbe.Windows]::IsVisible($HostProcess.Id)
                $report.windowSamples++
                Start-Sleep -Milliseconds 200
            }
        }
        $cli = Start-ProbeProcess $CliPath $arguments $true
        $stdout = $cli.StandardOutput.ReadToEndAsync()
        $stderr = $cli.StandardError.ReadToEndAsync()
        $clock = [Diagnostics.Stopwatch]::StartNew()
        do {
            if ($HostProcess -and -not $HostProcess.HasExited) {
                $report.visibleRootWindowObserved = $report.visibleRootWindowObserved -or
                    [EgoWindowsBetaProbe.Windows]::IsVisible($HostProcess.Id)
                $report.windowSamples++
            }
            $exited = $cli.WaitForExit(150)
        } until ($exited -or $clock.Elapsed.TotalSeconds -ge $TimeoutSeconds)

        if (-not $exited) {
            $report.code = 'native_cli_timeout'
            Stop-ProbeProcess $cli
        } else {
            $report.cliExit = $cli.ExitCode
        }
        foreach ($stream in @($stdout, $stderr)) {
            if (-not $stream.Wait(2000)) { continue }
            foreach ($line in ($stream.Result -split '\r?\n')) {
                if ($line.StartsWith('OBC_EGO_PROBE ')) {
                    try {
                        $report.checks += ConvertFrom-Json -InputObject $line.Substring(14)
                    } catch {
                        $report.code = 'malformed_probe_output'
                    }
                }
            }
        }
        if ($report.checks.Count -eq 0 -and -not $report.code) {
            $rawError = if ($stderr.Wait(2000)) { $stderr.Result } else { '' }
            $report.code = switch -Regex ($rawError) {
                'cannot find the app' { 'app_installation_not_resolved'; break }
                'bootstrap|failed to connect' { 'native_bootstrap_connection_failed'; break }
                'closed the connection' { 'native_connection_closed'; break }
                'onboarding|first.run' { 'app_onboarding_required'; break }
                'unknown (flag|option)|unrecognized (flag|option)' { 'native_cli_option_rejected'; break }
                default { 'no_probe_output' }
            }
        }
        $failed = @($report.checks | Where-Object { $_.status -eq 'fail' } | Select-Object -First 1)
        $passed = @($report.checks | Where-Object { $_.status -eq 'pass' } | ForEach-Object { $_.name })
        if (-not $report.code) {
            if ($failed.Count) { $report.code = $failed[0].code }
            elseif ($report.cliExit -ne 0) { $report.code = 'native_cli_nonzero_exit' }
            elseif ($HostProcess -and $HostProcess.HasExited) { $report.code = 'cold_browser_process_exited' }
            elseif ($report.visibleRootWindowObserved) { $report.code = 'visible_browser_window_observed' }
            elseif (
                'dom_and_screenshot' -notin $passed -or 'task_cleanup' -notin $passed -or
                ($HostProcess -and 'cold_host_identity' -notin $passed)
            ) { $report.code = 'native_probe_incomplete' }
            else { $report.status = 'pass' }
        }
    } catch {
        # Raw subprocess output, paths, command lines and screenshots stay off the report.
        $report.code = 'probe_process_or_output_error'
        $report.exceptionType = $_.Exception.GetType().Name
    } finally {
        if ($cli) {
            Stop-ProbeProcess $cli
            $cli.Dispose()
        }
    }
    return $report
}

if (-not $ReportPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $ReportPath = Join-Path $PSScriptRoot "ego-windows-beta-report-$stamp.json"
}

$report = [ordered]@{
    schema = 1
    testedAtUtc = [DateTime]::UtcNow.ToString('o')
    osVersion = [Environment]::OSVersion.Version.ToString()
    architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    powerShell = $PSVersionTable.PSVersion.ToString()
    headless = [ordered]@{ status = 'skip'; code = 'not_started' }
    native = [ordered]@{ status = 'skip'; code = 'not_started' }
}
$probeId = [Guid]::NewGuid().ToString('N').Substring(0, 12)
$scratch = Join-Path ([IO.Path]::GetTempPath()) "obc-ego-probe-$probeId"
$hostProcess = $null
$exitCode = 1

try {
    $cliPath = Find-EgoCli
    if (-not $cliPath) {
        $report.headless.code = 'ego_cli_not_found'
        $report.native.code = 'ego_cli_not_found'
        $exitCode = 2
    } else {
        $null = New-Item -ItemType Directory -Path $scratch
        $modulePath = Join-Path $scratch 'probe.mjs'
        [IO.File]::WriteAllText($modulePath, $probeModule, (New-Object Text.UTF8Encoding $false))

        try {
            $browserPath = Find-EgoBrowser $cliPath
            if (-not $browserPath) {
                $report.headless.code = 'ego_browser_exe_not_found_use_BrowserExe'
            } else {
                if (-not ('EgoWindowsBetaProbe.Windows' -as [type])) {
                    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace EgoWindowsBetaProbe {
    public static class Windows {
        private delegate bool WindowCallback(IntPtr hwnd, IntPtr param);
        [DllImport("user32.dll")]
        private static extern bool EnumWindows(WindowCallback callback, IntPtr param);
        [DllImport("user32.dll")]
        private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
        public static bool IsVisible(int processId) {
            bool found = false;
            EnumWindows(delegate(IntPtr hwnd, IntPtr param) {
                uint owner;
                GetWindowThreadProcessId(hwnd, out owner);
                found |= owner == (uint)processId && IsWindowVisible(hwnd);
                return !found;
            }, IntPtr.Zero);
            return found;
        }
    }
}
'@
                }
                Write-Host '[1/2] Testing a new headless Ego instance with a temporary profile...'
                $profile = Join-Path $scratch 'profile'
                $serverName = "obc-win-probe-$probeId"
                $arguments = ('--ego-server-name={0} "--user-data-dir={1}" --headless=new ' +
                    '--enable-automation --no-first-run --disable-background-networking --disable-component-update ' +
                    '--disable-sync --no-proxy-server about:blank') -f $serverName, $profile
                $hostProcess = Start-ProbeProcess $browserPath $arguments $false
                $report.headless = Invoke-NativeProbe $cliPath $modulePath @{
                    task = "OBC probe headless $probeId"
                    server = $serverName
                    browserPid = $hostProcess.Id
                    browserExe = $browserPath
                } $hostProcess
            }
        } catch {
            $report.headless = [ordered]@{
                status = 'fail'; code = 'cold_launch_error'; exceptionType = $_.Exception.GetType().Name
            }
        } finally {
            if ($hostProcess) {
                Stop-ProbeProcess $hostProcess -OwnedBrowserTree
                $hostProcess.Dispose()
            }
        }

        Write-Host '[2/2] Testing the installed default Ego instance. A normal window may open...'
        $report.native = Invoke-NativeProbe $cliPath $modulePath @{
            task = "OBC probe native $probeId"
        } $null

        if ($report.native.status -eq 'pass' -and $report.headless.status -eq 'pass') {
            $exitCode = 0
        } elseif ($report.headless.status -eq 'skip') {
            $exitCode = 2
        }
    }
} catch {
    $report.setupError = $_.Exception.GetType().Name
} finally {
    if (Test-Path -LiteralPath $scratch -PathType Container) {
        try {
            Remove-Item -LiteralPath $scratch -Recurse -Force
            $report.temporaryDirectoryRemoved = $true
        } catch {
            $report.temporaryDirectoryRemoved = $false
        }
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    foreach ($phase in 'headless', 'native') {
        Write-Host ("{0}: {1} {2}" -f $phase, $report[$phase].status, $report[$phase].code)
    }
    Write-Host "Report: $ReportPath"
}

exit $exitCode
