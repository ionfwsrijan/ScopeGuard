param(
    [Parameter(Mandatory = $true)]
    [string]$Audio,
    [string]$Output = "demo/video/scopeguard-demo-final.mp4"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$video = Join-Path $repoRoot "demo/video/scopeguard-demo-4k60-silent.mp4"
$captions = Join-Path $repoRoot "demo/video/narration.srt"
$audioPath = (Resolve-Path $Audio).Path
$outputPath = Join-Path $repoRoot $Output
$outputDir = Split-Path -Parent $outputPath
if (-not (Test-Path -LiteralPath $video)) { throw "Silent 4K master not found: $video" }
if (-not (Test-Path -LiteralPath $captions)) { throw "Caption file not found: $captions" }
if (-not (Test-Path -LiteralPath $outputDir)) { New-Item -ItemType Directory -Path $outputDir | Out-Null }

$ffmpeg = (& py -3.13 -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())").Trim()
if (-not (Test-Path -LiteralPath $ffmpeg)) { throw "FFmpeg not found: $ffmpeg" }

& $ffmpeg -hide_banner -y -i $video -i $audioPath -i $captions `
    -map 0:v:0 -map 1:a:0 -map 2:0 `
    -c:v copy -c:a aac -b:a 320k -af apad -c:s mov_text `
    -shortest -movflags +faststart $outputPath
if ($LASTEXITCODE -ne 0) { throw "FFmpeg audio mux failed with exit code $LASTEXITCODE" }
Write-Output "Created $outputPath"
