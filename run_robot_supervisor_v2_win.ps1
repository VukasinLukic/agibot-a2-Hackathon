Param(
    [int]$Port = $(if ($env:ROBOT_SUPERVISOR_PORT) { [int]$env:ROBOT_SUPERVISOR_PORT } else { 8080 }),
    [string]$HostAddress = $(if ($env:ROBOT_SUPERVISOR_HOST) { $env:ROBOT_SUPERVISOR_HOST } else { "0.0.0.0" })
)

$ErrorActionPreference = "Stop"

$WorkDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $WorkDir

# Optional: preload .env into the process environment (FastAPI also loads it).
$EnvPath = Join-Path $WorkDir ".env"
function Parse-EnvLine([string]$line) {
    $line = $line.Trim()
    if (-not $line -or $line.StartsWith("#")) { return $null }

    $inQuotes = $false
    $result = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $line.Length; $i++) {
        $ch = $line[$i]
        if ($ch -eq '"') {
            $inQuotes = -not $inQuotes
            $null = $result.Append($ch)
            continue
        }
        if (-not $inQuotes -and $ch -eq '#') {
            break
        }
        $null = $result.Append($ch)
    }

    $clean = $result.ToString().Trim()
    if (-not $clean) { return $null }

    $parts = $clean -split "=", 2
    if ($parts.Count -ne 2) { return $null }

    $key = $parts[0].Trim()
    $value = $parts[1].Trim()

    if ($value.StartsWith('"') -and $value.EndsWith('"') -and $value.Length -ge 2) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    return @{ Key = $key; Value = $value }
}

if (Test-Path $EnvPath) {
    Get-Content $EnvPath | ForEach-Object {
        $parsed = Parse-EnvLine $_
        if ($null -ne $parsed) {
            [System.Environment]::SetEnvironmentVariable($parsed.Key, $parsed.Value, "Process")
        }
    }
}

# Activate venv
$VenvActivate = "c:\Users\rokma\Documents\CTSI\HumanoidTest\MicStreamTest\.venv12\Scripts\Activate.ps1"
if (-not (Test-Path $VenvActivate)) {
    Write-Error "Virtualenv not found at $VenvActivate. Create it first."
    exit 1
}
. $VenvActivate

$ApiScript = Join-Path $WorkDir "robot_supervisor_v2\run_api.py"
if (-not (Test-Path $ApiScript)) {
    Write-Error "API entrypoint not found: $ApiScript"
    exit 1
}

python $ApiScript --host $HostAddress --port $Port
