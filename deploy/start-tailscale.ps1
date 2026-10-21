# Serve the backend from this machine through Tailscale Funnel.
# Funnel forwards https://<machine>.<tailnet>.ts.net to 127.0.0.1:8000, so the
# API binds to localhost only - nothing is exposed except through the tunnel.
# Usage (from the project root):  powershell -File deploy\start-tailscale.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

# Same guarantee as the Docker image: fail instead of silently writing vectors
# to a local Chroma store the deployed app would otherwise depend on.
$env:CHROMA_REQUIRE_CLOUD = "true"

# Idempotent: (re)points the public HTTPS URL at the API port.
tailscale funnel --bg 8000
tailscale funnel status

uv run uvicorn api:app --host 127.0.0.1 --port 8000
