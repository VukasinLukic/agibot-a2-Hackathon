<#
Laptop side of the robot vision kit (docs/ROBOT_VISION_KIT.md). Only scp/ssh file
copies and the calibration click window; nothing here moves the robot.

  powershell -ExecutionPolicy Bypass -File scripts\laptop_vision.ps1 <Action>

  Wheels     download pydantic wheels for the robot (aarch64, Python 3.10)
  PushCode   copy table_tennis (without var) + scripts/robot_vision.sh; robot keeps a backup tgz
  PushKit    copy robot_vision.sh, new + previous BallNet weights and the wheels
  PullFrame  fetch kadar.png from the robot
  Calibrate  click the six points on kadar.png, then send table.json + table.png to the robot
  PullClips  fetch recorded .ttclip files from the robot
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Wheels', 'PushCode', 'PushKit', 'PullFrame', 'Calibrate', 'PullClips')]
    [string]$Action,
    [string]$Robot = 'agi@192.168.2.50',
    [string]$RemoteRepo = '/agibot/humanoid-platform',
    [string]$NewWeights = 'table_tennis\var\vision\train\ballnet.onnx',
    [string]$PrevWeights = 'table_tennis\var\vision\kit\ballnet_prev.onnx',
    [string]$Python = '.venv-vision\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Kit = Join-Path $Repo 'table_tennis\var\vision\kit'
$RemoteVision = "$RemoteRepo/table_tennis/var/vision"
New-Item -ItemType Directory -Force $Kit | Out-Null

# Print the exact command, run it, stop on a non-zero exit code.
function Invoke-Step {
    param([string]$Exe, [string[]]$Arguments)
    Write-Host ('+ ' + $Exe + ' ' + ($Arguments -join ' ')) -ForegroundColor Cyan
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe exited with $LASTEXITCODE" }
}

function Get-Sibling {
    param([string]$Path, [string]$Extension)
    return [System.IO.Path]::ChangeExtension($Path, $Extension)
}

switch ($Action) {
    'Wheels' {
        $wheels = Join-Path $Kit 'wheels'
        Invoke-Step $Python @('-m', 'pip', 'download', 'pydantic>=2.6,<3', '--only-binary=:all:',
            '--platform', 'manylinux2014_aarch64', '--python-version', '3.10', '--implementation', 'cp',
            '-d', $wheels, '-q')
        Get-ChildItem $wheels -Filter *.whl | ForEach-Object { $_.Name }
    }
    'PushCode' {
        $archive = Join-Path $Kit 'tt_code.tgz'
        Invoke-Step 'tar.exe' @('-czf', $archive, '--exclude', 'table_tennis/var', '--exclude', '__pycache__',
            'table_tennis', 'scripts/robot_vision.sh')
        Invoke-Step 'scp' @($archive, "${Robot}:~/tt_code.tgz")
        $remote = "cd $RemoteRepo && tar czf ~/tt_backup_`$(date +%Y%m%d-%H%M%S).tgz --exclude=table_tennis/var table_tennis scripts 2>/dev/null; " +
            "tar xzf ~/tt_code.tgz && sed -i 's/\r`$//' scripts/robot_vision.sh && chmod +x scripts/robot_vision.sh && ls -l ~/tt_backup_*.tgz | tail -n 1"
        Invoke-Step 'ssh' @($Robot, $remote)
    }
    'PushKit' {
        foreach ($file in @($NewWeights, (Get-Sibling $NewWeights '.npz'), $PrevWeights, (Get-Sibling $PrevWeights '.npz'))) {
            if (-not (Test-Path $file)) { throw "missing $file" }
        }
        Get-FileHash -Algorithm MD5 $NewWeights, $PrevWeights | Format-Table Hash, Path -AutoSize
        $staged = Join-Path $Kit 'push'
        New-Item -ItemType Directory -Force $staged | Out-Null
        Copy-Item $NewWeights (Join-Path $staged 'ballnet.onnx') -Force
        Copy-Item (Get-Sibling $NewWeights '.npz') (Join-Path $staged 'ballnet.npz') -Force
        Copy-Item $PrevWeights (Join-Path $staged 'ballnet_prev.onnx') -Force
        Copy-Item (Get-Sibling $PrevWeights '.npz') (Join-Path $staged 'ballnet_prev.npz') -Force
        Invoke-Step 'ssh' @($Robot, "mkdir -p $RemoteRepo/scripts $RemoteVision/wheels")
        $files = @('scripts\robot_vision.sh') + (Get-ChildItem $staged -File | ForEach-Object { $_.FullName })
        Invoke-Step 'scp' ($files + @("${Robot}:$RemoteVision/"))
        $wheels = Join-Path $Kit 'wheels'
        if (Test-Path $wheels) {
            Invoke-Step 'scp' (@(Get-ChildItem $wheels -Filter *.whl | ForEach-Object { $_.FullName }) + @("${Robot}:$RemoteVision/wheels/"))
        }
        $remote = "mv $RemoteVision/robot_vision.sh $RemoteRepo/scripts/ && sed -i 's/\r`$//' $RemoteRepo/scripts/robot_vision.sh && " +
            "chmod +x $RemoteRepo/scripts/robot_vision.sh && md5sum $RemoteVision/ballnet*.onnx"
        Invoke-Step 'ssh' @($Robot, $remote)
    }
    'PullFrame' {
        Invoke-Step 'scp' @("${Robot}:$RemoteVision/kadar.png", (Join-Path $Kit 'kadar.png'))
    }
    'Calibrate' {
        $frame = Join-Path $Kit 'kadar.png'
        if (-not (Test-Path $frame)) { throw "missing $frame (run PullFrame first)" }
        $out = Join-Path $Kit 'table.json'
        Invoke-Step $Python @('-m', 'table_tennis.vision.calibrate', '--image', $frame, '--out', $out)
        Invoke-Step 'scp' @($out, (Join-Path $Kit 'table.png'), "${Robot}:$RemoteVision/")
        Write-Host "Robot: scripts/robot_vision.sh cal-id   (calibration_id ide u aplikaciju)"
    }
    'PullClips' {
        $clips = Join-Path $Repo 'table_tennis\var\vision\clips'
        New-Item -ItemType Directory -Force $clips | Out-Null
        Invoke-Step 'scp' @("${Robot}:$RemoteVision/clips/*.ttclip", $clips)
        Get-ChildItem $clips -Filter *.ttclip | Format-Table Name, Length -AutoSize
    }
}
