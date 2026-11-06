param(
    [int]$Port = 18787,
    [string]$Browser = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scopeguard = Join-Path $repoRoot ".venv\Scripts\scopeguard.exe"
if (-not (Test-Path -LiteralPath $scopeguard)) {
    throw "Install the project first; expected $scopeguard"
}

if (-not $Browser) {
    $candidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
    )
    $Browser = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Browser -or -not (Test-Path -LiteralPath $Browser)) {
    throw "Chrome or Edge was not found. Pass its executable path with -Browser."
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$demoHome = Join-Path ([System.IO.Path]::GetTempPath()) "scopeguard-capture-$stamp-$PID"
$serverLog = Join-Path ([System.IO.Path]::GetTempPath()) "scopeguard-capture-$stamp-$PID.out.log"
$serverErrorLog = Join-Path ([System.IO.Path]::GetTempPath()) "scopeguard-capture-$stamp-$PID.err.log"
$serverArgs = "--root `"$repoRoot`" --home `"$demoHome`" serve --demo --port $Port"
$startedAt = Get-Date
$server = Start-Process -FilePath $scopeguard -ArgumentList $serverArgs `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput $serverLog `
    -RedirectStandardError $serverErrorLog

try {
    $baseUrl = "http://127.0.0.1:$Port"
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($server.HasExited) {
            throw "The demo server exited before it became ready. See $serverLog"
        }
        try {
            $null = Invoke-WebRequest -UseBasicParsing -Uri $baseUrl -TimeoutSec 2
            $ready = $true
            break
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) {
        throw "The demo server did not become ready. See $serverLog"
    }

    $receiptId = (Get-Content (Join-Path $repoRoot "demo\receipt.json") -Raw | ConvertFrom-Json).body.receipt_id
    $captures = @(
        @{ File = "01-room.png"; Url = "$baseUrl/"; Height = 1800 },
        @{ File = "02-case.png"; Url = "$baseUrl/engagements/northwind-2026"; Height = 2400 },
        @{ File = "03-receipt.png"; Url = "$baseUrl/change-orders/$receiptId"; Height = 2400 }
    )
    $imageDir = Join-Path $repoRoot "demo\images"
    foreach ($capture in $captures) {
        $destination = Join-Path $imageDir $capture.File
        $profile = Join-Path ([System.IO.Path]::GetTempPath()) "scopeguard-browser-$stamp-$PID-$($capture.File)"
        $captureTemp = Join-Path ([System.IO.Path]::GetTempPath()) "scopeguard-image-$stamp-$PID-$($capture.File)"
        $browserArgs = @(
            "--headless=new", "--disable-gpu", "--disable-cache", "--hide-scrollbars",
            "--no-first-run", "--window-size=1440,$($capture.Height)",
            "--user-data-dir=$profile", "--screenshot=$captureTemp", $capture.Url
        )
        $quotedBrowserArgs = $browserArgs | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }
        $browserProcess = Start-Process -FilePath $Browser -ArgumentList ($quotedBrowserArgs -join ' ') `
            -WindowStyle Hidden -PassThru -Wait
        if ($browserProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $captureTemp)) {
            throw "Failed to capture $($capture.File) with $Browser"
        }
        Move-Item -LiteralPath $captureTemp -Destination $destination -Force
        if ((Get-Item -LiteralPath $destination).LastWriteTime -lt $startedAt) {
            throw "Screenshot was not refreshed: $destination"
        }
        Write-Output "Captured $destination"
    }
}
finally {
    if (-not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
