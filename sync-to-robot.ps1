param([switch]$Push)
$ErrorActionPreference = 'Stop'
function g { git @args; if ($LASTEXITCODE -ne 0) { throw "Git command failed: $args" } }
Set-Location $PSScriptRoot
$TeamBranch = 'a2-hackathon-team4'
$ExpectedRemote = 'https://github.com/leksaas/a2-hackathon.git'
if ((g branch --show-current) -ne $TeamBranch) { throw "Stay on $TeamBranch; no automatic checkout." }
if (g status --porcelain) { throw 'Review/commit local changes first.' }
$PushUrls = @(g remote get-url --push --all origin)
if ($PushUrls.Count -ne 1 -or $PushUrls[0] -ne $ExpectedRemote) { throw 'Unexpected origin push URL; stop and inspect remotes.' }
g fetch origin $TeamBranch
g merge --ff-only FETCH_HEAD
$CheckPython = Join-Path $PSScriptRoot '.venv-tt\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $CheckPython)) { $CheckPython = 'python' }
& $CheckPython scripts/check_git_secrets.py
if ($LASTEXITCODE -ne 0) { throw 'Secret guard failed.' }
$Refspec = "HEAD:refs/heads/$TeamBranch"
g -c push.followTags=false -c remote.origin.mirror=false push --dry-run origin $Refspec
if ($Push) {
    g -c push.followTags=false -c remote.origin.mirror=false push -u origin $Refspec
    Write-Host "Pushed ONLY $ExpectedRemote -> $TeamBranch. No robot deployment/restart." -ForegroundColor Green
} else {
    Write-Host 'Dry-run only. Use -Push after review. Never pushes main.'
}
