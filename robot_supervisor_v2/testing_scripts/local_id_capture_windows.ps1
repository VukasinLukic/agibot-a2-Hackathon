<#
.SYNOPSIS
Run the local ID/card capture OpenCV harness on Windows.

.EXAMPLE
.\robot_supervisor_v2\testing_scripts\local_id_capture_windows.ps1 -ListDevices

.EXAMPLE
.\robot_supervisor_v2\testing_scripts\local_id_capture_windows.ps1 -Camera 0 -Relaxed -Debug

.EXAMPLE
.\robot_supervisor_v2\testing_scripts\local_id_capture_windows.ps1 -Camera 1 -Portrait -Debug
#>

param(
    [string]$Python,
    [string]$Camera,
    [int]$Width,
    [int]$Height,
    [double]$Fps,
    [string]$SaveDir,
    [string]$RequestId,
    [string]$Source,
    [switch]$ListDevices,
    [switch]$Headless,
    [switch]$Debug,
    [switch]$ContinueAfterCapture,
    [switch]$Relaxed,
    [switch]$Portrait,
    [int]$PortraitDebugMax,
    [string]$DebugSaveDir,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CaptureArgs
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

if (-not $Python) {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $Python = $VenvPython
    } else {
        $Python = "python"
    }
}

$ForwardArgs = @()
if ($Camera) {
    $ForwardArgs += @("--camera", $Camera)
}
if ($Width) {
    $ForwardArgs += @("--width", $Width.ToString())
}
if ($Height) {
    $ForwardArgs += @("--height", $Height.ToString())
}
if ($Fps) {
    $ForwardArgs += @("--fps", $Fps.ToString([Globalization.CultureInfo]::InvariantCulture))
}
if ($SaveDir) {
    $ForwardArgs += @("--save-dir", $SaveDir)
}
if ($RequestId) {
    $ForwardArgs += @("--request-id", $RequestId)
}
if ($Source) {
    $ForwardArgs += @("--source", $Source)
}
if ($ListDevices) {
    $ForwardArgs += "--list-devices"
}
if ($Headless) {
    $ForwardArgs += "--headless"
}
if ($Debug) {
    $ForwardArgs += "--debug"
}
if ($ContinueAfterCapture) {
    $ForwardArgs += "--continue-after-capture"
}
if ($Portrait) {
    $ForwardArgs += "--portrait-debug"
}
if ($PortraitDebugMax) {
    $ForwardArgs += @("--portrait-debug-max", $PortraitDebugMax.ToString())
}
if ($DebugSaveDir) {
    $ForwardArgs += @("--debug-save-dir", $DebugSaveDir)
}
if ($CaptureArgs) {
    $ForwardArgs += $CaptureArgs
}

$HasSourceArg = $false
foreach ($Arg in $ForwardArgs) {
    if ($Arg -eq "--source" -or $Arg.StartsWith("--source=")) {
        $HasSourceArg = $true
        break
    }
}
if (-not $HasSourceArg) {
    $ForwardArgs += @("--source", "windows-local")
}

function Set-DefaultEnv {
    param(
        [string]$Name,
        [string]$Value
    )
    if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

if ($Relaxed) {
    Set-DefaultEnv "VISION_CARD_CAPTURE_MIN_AREA_RATIO" "0.04"
    Set-DefaultEnv "VISION_CARD_CAPTURE_MIN_BLUR" "25"
    Set-DefaultEnv "VISION_CARD_CAPTURE_STABILITY_FRAMES" "2"
    Set-DefaultEnv "VISION_CARD_CAPTURE_MAX_BRIGHTNESS" "245"
    Set-DefaultEnv "VISION_CARD_CAPTURE_MAX_GLARE_RATIO" "0.35"
} else {
    Set-DefaultEnv "VISION_CARD_CAPTURE_MIN_AREA_RATIO" "0.08"
    Set-DefaultEnv "VISION_CARD_CAPTURE_MIN_BLUR" "80"
    Set-DefaultEnv "VISION_CARD_CAPTURE_STABILITY_FRAMES" "3"
}

Set-DefaultEnv "VISION_CARD_CAPTURE_JPEG_QUALITY" "90"
Set-DefaultEnv "VISION_CARD_CAPTURE_OUTPUT_WIDTH" "856"
Set-DefaultEnv "VISION_CARD_CAPTURE_OUTPUT_HEIGHT" "540"

$CaptureScript = Join-Path $ScriptDir "local_id_capture.py"

Write-Host "Running local ID capture"
Write-Host "  Python: $Python"
Write-Host "  Script: $CaptureScript"
Write-Host ""

& $Python $CaptureScript @ForwardArgs
exit $LASTEXITCODE
