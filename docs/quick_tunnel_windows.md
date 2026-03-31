# Quick Tunnel auto-run on Windows

This setup keeps your bot stack and Cloudflare Quick Tunnel running without relying on an open terminal window.

## What this provides

- Starts docker compose automatically
- Starts cloudflared Quick Tunnel automatically
- Restarts cloudflared if it exits
- Saves current Quick Tunnel URL to runtime/quick_tunnel_url.txt
- Optionally updates .env TUNNEL_PUBLIC_BASE_URL
- Optionally recreates compose service when URL changes

## Prerequisites

- Docker Desktop is installed and usable from PowerShell
- cloudflared is installed and available in PATH
- .env exists in repository root

## One-time setup

Run PowerShell as Administrator in repository root.

Register startup task:

powershell -ExecutionPolicy Bypass -File scripts/register_startup_task.ps1 -SyncUrlToEnv -RestartComposeOnUrlChange

Start now (without reboot):

Start-ScheduledTask -TaskName ClassInformationBotQuickTunnel

## Check status

Current tunnel URL:

Get-Content runtime/quick_tunnel_url.txt

Worker log:

Get-Content logs/quick_tunnel_worker.log -Tail 100

Tunnel session logs:

Get-ChildItem logs/quick_tunnel_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1

## Remove startup task

powershell -ExecutionPolicy Bypass -File scripts/unregister_startup_task.ps1

## Notes

- Quick Tunnel URL changes whenever cloudflared process restarts.
- This flow auto-updates runtime/quick_tunnel_url.txt and can auto-sync .env.
- If you do not need .env updates, omit -SyncUrlToEnv.
