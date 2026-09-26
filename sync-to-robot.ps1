function g { git @args; if ($LASTEXITCODE -ne 0) { throw "Neuspelo: git $args" } }

Set-Location $PSScriptRoot
g fetch nas
g fetch origin
g checkout a2-hackathon-team4
g merge --ff-only origin/a2-hackathon-team4   # povuci ako je neko nešto dodao na branch
g merge nas/main -m "Sync from our repo for robot testing"
g push origin a2-hackathon-team4
Write-Host "Gotovo: nas/main je na a2-hackathon-team4" -ForegroundColor Green