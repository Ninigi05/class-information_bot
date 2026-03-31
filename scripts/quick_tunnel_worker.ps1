param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$WebPort = 8000,
    [int]$RestartDelaySeconds = 5,
    [int]$UrlWaitTimeoutSeconds = 90,
    [switch]$SyncUrlToEnv,
    [switch]$RestartComposeOnUrlChange
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Write-Host $line
    if ($script:WorkerLogPath) {
        Add-Content -Path $script:WorkerLogPath -Value $line -Encoding UTF8
    }
}

function Update-EnvValue {
    param(
        [string]$EnvPath,
        [string]$Key,
        [string]$Value
    )

    if (-not (Test-Path $EnvPath)) {
        throw ".env not found: $EnvPath"
    }

    $content = Get-Content -Path $EnvPath -Raw -Encoding UTF8
    $pattern = "(?m)^" + [Regex]::Escape($Key) + "=.*$"
    $replacement = "$Key=$Value"

    if ($content -match $pattern) {
        $updated = [Regex]::Replace($content, $pattern, $replacement)
    }
    else {
        if ($content.Length -gt 0 -and -not $content.EndsWith("`n")) {
            $content += "`r`n"
        }
        $updated = $content + "$replacement`r`n"
    }

    Set-Content -Path $EnvPath -Value $updated -Encoding UTF8
}

function Ensure-DockerComposeUp {
    param([string]$Root)
    Push-Location $Root
    try {
        Write-Log "Starting docker compose stack"
        docker compose up -d | Out-Null
    }
    finally {
        Pop-Location
    }
}

function Start-QuickTunnelProcess {
    param(
        [string]$Root,
        [int]$Port,
        [string]$StdoutLogPath,
        [string]$StderrLogPath
    )

    $cloudflaredCmd = Get-Command cloudflared -ErrorAction Stop
    $args = @("tunnel", "--url", "http://localhost:$Port", "--no-autoupdate")

    Write-Log "Launching cloudflared Quick Tunnel"
    $proc = Start-Process -FilePath $cloudflaredCmd.Source `
        -ArgumentList $args `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $StdoutLogPath `
        -RedirectStandardError $StderrLogPath `
        -PassThru `
        -WindowStyle Hidden

    return $proc
}

function Wait-ForQuickTunnelUrl {
    param(
        [string[]]$LogPaths,
        [int]$TimeoutSeconds
    )

    $regex = "https://[-a-z0-9]+\.trycloudflare\.com"
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        foreach ($logPath in $LogPaths) {
            if (Test-Path $logPath) {
                $raw = Get-Content -Path $logPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
                if ($raw) {
                    $m = [Regex]::Match($raw, $regex)
                    if ($m.Success) {
                        return $m.Value.TrimEnd('/')
                    }
                }
            }
        }
        Start-Sleep -Milliseconds 700
    }

    return ""
}

$RepoRoot = (Resolve-Path $RepoRoot).Path
$EnvPath = Join-Path $RepoRoot ".env"
$RuntimeDir = Join-Path $RepoRoot "runtime"
$TunnelUrlPath = Join-Path $RuntimeDir "quick_tunnel_url.txt"
$LogDir = Join-Path $RepoRoot "logs"
$WorkerLogPath = Join-Path $LogDir "quick_tunnel_worker.log"
$script:WorkerLogPath = $WorkerLogPath

if (-not (Test-Path $RuntimeDir)) { New-Item -Path $RuntimeDir -ItemType Directory | Out-Null }
if (-not (Test-Path $LogDir)) { New-Item -Path $LogDir -ItemType Directory | Out-Null }

Write-Log "Worker started RepoRoot=$RepoRoot WebPort=$WebPort"

$lastUrl = ""
while ($true) {
    try {
        Ensure-DockerComposeUp -Root $RepoRoot

        $sessionStamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $tunnelStdoutLogPath = Join-Path $LogDir "quick_tunnel_${sessionStamp}.out.log"
        $tunnelStderrLogPath = Join-Path $LogDir "quick_tunnel_${sessionStamp}.err.log"
        $proc = Start-QuickTunnelProcess `
            -Root $RepoRoot `
            -Port $WebPort `
            -StdoutLogPath $tunnelStdoutLogPath `
            -StderrLogPath $tunnelStderrLogPath

        $url = Wait-ForQuickTunnelUrl -LogPaths @($tunnelStdoutLogPath, $tunnelStderrLogPath) -TimeoutSeconds $UrlWaitTimeoutSeconds
        if ($url) {
            Write-Log "Quick Tunnel URL: $url"
            Set-Content -Path $TunnelUrlPath -Value $url -Encoding UTF8

            if ($SyncUrlToEnv) {
                Update-EnvValue -EnvPath $EnvPath -Key "TUNNEL_PUBLIC_BASE_URL" -Value $url
                Write-Log "Updated .env TUNNEL_PUBLIC_BASE_URL"

                if ($RestartComposeOnUrlChange -and $url -ne $lastUrl) {
                    Push-Location $RepoRoot
                    try {
                        Write-Log "Recreating compose service to apply env change"
                        docker compose up -d --force-recreate class-information-bot | Out-Null
                    }
                    finally {
                        Pop-Location
                    }
                }
            }

            $lastUrl = $url
        }
        else {
            Write-Log "Could not find tunnel URL in log within timeout"
        }

        Write-Log "Monitoring cloudflared process pid=$($proc.Id)"
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 2
        }

        Write-Log "cloudflared exited with code $($proc.ExitCode)"
    }
    catch {
        Write-Log "Worker error: $($_.Exception.Message)"
    }

    Write-Log "Restarting loop after $RestartDelaySeconds sec"
    Start-Sleep -Seconds $RestartDelaySeconds
}
