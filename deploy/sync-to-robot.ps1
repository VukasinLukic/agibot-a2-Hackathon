<#
.SYNOPSIS
    Salje nas kod na hakaton granu a2-hackathon-team4 za testiranje na robotu.

.DESCRIPTION
    Svakodnevni rad ide u nas repo (VukasinLukic/agibot-a2-Hackathon). Kada se
    testira na robotu, ova skripta spaja nas GitHub main u granu
    a2-hackathon-team4 na leksaas/a2-hackathon i pushuje je.

    Pre pusha proverava greske koje su se vec desavale:
      - pokrenuto iz pogresnog foldera / nedostaju remote-ovi
      - zaostali .git/index.lock ili nezavrsen merge/rebase/cherry-pick
      - necommit-ovane izmene u deploy folderu
      - commit-ovi napravljeni direktno u deploy folderu
      - izmene koje je neko drugi (npr. drugi tim) pushovao na nasu granu
      - ne-pracene kopije fajlova koje bi merge pregazio
      - merge konflikti (merge se automatski prekida, nista ne ostaje napola)
      - konfliktni markeri, tajne (.env, kljucevi, tokeni), preveliki fajlovi
      - Python fajlovi koji se ne parsiraju
    Nikada ne koristi --force i nikada ne pushuje na granu drugog tima.

    Salje se ono sto je na GitHub-u, NE lokalne izmene sa racunara: pre
    pokretanja pushujte nas repo.

.PARAMETER RepoPath
    Folder klona leksaas/a2-hackathon. Podrazumevano: trenutni folder.

.PARAMETER SourceBranch
    Grana naseg repoa koja se salje. Podrazumevano: main.

.PARAMETER DryRun
    Uradi sve provere i probni merge, ali nista ne commit-uj i ne pushuj.

.EXAMPLE
    cd "C:\Users\Tea\Downloads\mozda cak bude i radilo\a2-hackathon"
    powershell -ExecutionPolicy Bypass -File .\deploy\sync-to-robot.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\deploy\sync-to-robot.ps1 -DryRun
#>
[CmdletBinding()]
Param(
    [string]$RepoPath = (Get-Location).Path,
    [string]$SourceBranch = "main",
    [switch]$DryRun
)

$ErrorActionPreference = "Continue"

# Fixed on purpose: this script must never push to another team's branch.
$TargetBranch = "a2-hackathon-team4"
$HackathonRepoPattern = 'github\.com[:/]+leksaas/a2-hackathon(\.git)?/?$'
$OurRepoPattern = 'github\.com[:/]+VukasinLukic/agibot-a2-Hackathon(\.git)?/?$'
$OurRepoUrl = "https://github.com/VukasinLukic/agibot-a2-Hackathon.git"
$OurRemoteDefaultName = "nas"

$HardLimitBytes = 95MB   # GitHub rejects files over 100 MB.
$WarnLimitBytes = 20MB

# The hackathon repo is readable by the organizers and other teams.
$SecretPatterns = [ordered]@{
    "privatni kljuc"   = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    "API kljuc sk-..." = 'sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}'
    "AWS kljuc"        = 'AKIA[0-9A-Z]{16}'
    "GitHub token"     = '(ghp|gho|ghu|ghs)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}'
    "Google API kljuc" = 'AIza[0-9A-Za-z_-]{35}'
    "Slack token"      = 'xox[abprs]-[A-Za-z0-9-]{10,}'
    "lozinka/secret"   = '(?i)(api_?secret|client_?secret|account_?key|password)\s*[:=]\s*["'']?[A-Za-z0-9+/=_-]{16,}'
}
$SensitiveFilePattern = '(^|/)(\.env(\.[^/]*)?|[^/]*\.(pem|key|pfx|p12)|id_rsa[^/]*|[^/]*credentials[^/]*\.json|config\.local\.yaml)$|^robot_supervisor_v2/config\.yaml$'
$SensitiveFileAllowPattern = '\.(example|template|sample|dist)$'

$script:MergeStarted = $false
$script:OriginalBranch = $null
$script:SwitchedBranch = $false

function Write-Step([string]$Message) { Write-Host ""; Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    PAZNJA  $Message" -ForegroundColor Yellow }
function Write-List($Items, [int]$Max = 30) {
    $all = @($Items)
    foreach ($item in ($all | Select-Object -First $Max)) { Write-Host "      $item" }
    if ($all.Count -gt $Max) { Write-Host "      ... i jos $($all.Count - $Max)" }
}

function Invoke-GitChecked {
    # Runs git and returns stdout lines; throws on a non-zero exit code.
    $out = & git -c core.quotepath=false @args
    if ($LASTEXITCODE -ne 0) { throw "Komanda 'git $($args -join ' ')' nije uspela (exit kod $LASTEXITCODE)." }
    return $out
}

function Test-Git {
    & git @args *> $null
    return ($LASTEXITCODE -eq 0)
}

function Confirm-OrStop([string]$Question) {
    $answer = [string](Read-Host "    $Question [d/N]")
    if ($answer -notmatch '^\s*(d|da|y|yes)\s*$') { throw "Prekinuto. Nista nije pushovano." }
}

function Find-Remote([string]$Pattern) {
    foreach ($line in @(Invoke-GitChecked remote -v)) {
        $parts = $line -split '\s+'
        if ($parts.Count -ge 3 -and $parts[2] -eq '(fetch)' -and $parts[1] -match $Pattern) { return $parts[0] }
    }
    return $null
}

function Invoke-Fetch([string]$Remote, [string]$Branch) {
    & git fetch --no-tags $Remote $Branch
    if ($LASTEXITCODE -ne 0) {
        throw "Ne mogu da preuzmem '$Branch' sa '$Remote'. Proveri internet, pristup repou (git login za github.com) i da li grana '$Branch' postoji na GitHub-u."
    }
}

# Newest merge on the target's first-parent chain that brought in one of our commits.
function Find-LastSyncPoint([string]$HackRef, [string]$OursRemote) {
    foreach ($merge in @(Invoke-GitChecked rev-list --merges --first-parent --max-count=100 $HackRef)) {
        $parents = @((Invoke-GitChecked rev-list --parents -n 1 $merge) -split ' ')
        for ($i = 2; $i -lt $parents.Count; $i++) {
            $hit = @(Invoke-GitChecked for-each-ref --count=1 --contains $parents[$i] "--format=%(refname)" "refs/remotes/$OursRemote")
            if ($hit.Count -gt 0 -and $hit[0]) { return $merge }
        }
    }
    return $null
}

function Find-Python {
    foreach ($candidate in @(@('py', '-3'), @('python'), @('python3'))) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        # Skips the Windows Store "python" stub, which exists but does not run.
        & $candidate[0] @rest -c "import sys" *> $null
        if ($LASTEXITCODE -eq 0) { return , $candidate }
    }
    return $null
}

function Test-PythonSyntax([string[]]$Files) {
    $python = Find-Python
    if (-not $python) {
        Write-Warn "Python nije pronadjen - preskacem proveru sintakse .py fajlova."
        return @()
    }
    $checker = @'
import ast, sys, warnings
warnings.simplefilter("ignore")
bad = 0
with open(sys.argv[1], encoding="utf-8") as fh:
    paths = [p for p in fh.read().splitlines() if p]
for p in paths:
    try:
        with open(p, "rb") as src:
            ast.parse(src.read(), p)
    except SyntaxError as e:
        bad += 1
        print("%s:%s: %s" % (p, e.lineno, e.msg))
    except (OSError, ValueError) as e:
        bad += 1
        print("%s: nije moguce procitati (%s)" % (p, e))
sys.exit(1 if bad else 0)
'@
    $tmp = [IO.Path]::GetTempPath()
    $checkerPath = Join-Path $tmp ("sync-to-robot-check-" + [guid]::NewGuid() + ".py")
    $listPath = Join-Path $tmp ("sync-to-robot-files-" + [guid]::NewGuid() + ".txt")
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    try {
        [IO.File]::WriteAllText($checkerPath, $checker, $utf8)
        [IO.File]::WriteAllLines($listPath, $Files, $utf8)
        $rest = @($python | Select-Object -Skip 1)
        $out = @(& $python[0] @rest $checkerPath $listPath)
        if ($LASTEXITCODE -ne 0) { return @($out | ForEach-Object { "$_ : Python sintaksna greska" }) }
        return @()
    }
    finally {
        Remove-Item -LiteralPath $checkerPath, $listPath -ErrorAction SilentlyContinue
    }
}

function Invoke-Sync {
    Write-Step "[1/8] Provera okruzenja"
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git nije instaliran ili nije u PATH-u." }
    if (-not (Test-Path -LiteralPath $RepoPath)) { throw "Folder ne postoji: $RepoPath" }
    Set-Location -LiteralPath $RepoPath
    if (-not (Test-Git rev-parse --show-toplevel)) {
        throw "'$RepoPath' nije git folder. Pokreni skriptu iz foldera klona leksaas/a2-hackathon ili prosledi -RepoPath."
    }
    Set-Location -LiteralPath (Invoke-GitChecked rev-parse --show-toplevel)
    $gitDir = Invoke-GitChecked rev-parse --absolute-git-dir

    $lock = Join-Path $gitDir "index.lock"
    if (Test-Path -LiteralPath $lock) {
        $running = @(Get-Process -Name "git*" -ErrorAction SilentlyContinue | Where-Object { $_.Id -ne $PID })
        if ($running.Count -gt 0) {
            Write-Warn "Postoji .git/index.lock, a radi i neki git proces: $((@($running | ForEach-Object { $_.ProcessName }) | Sort-Object -Unique) -join ', ')"
            Write-Warn "Zatvori VS Code / GitHub Desktop / druge terminale koji koriste git u ovom folderu."
            Confirm-OrStop "Siguran si da nista ne radi sa gitom u ovom folderu? Brisem index.lock?"
        }
        Remove-Item -LiteralPath $lock -Force
        Write-Ok "Obrisan zaostali .git/index.lock"
    }

    $inProgress = @()
    if (Test-Path -LiteralPath (Join-Path $gitDir "MERGE_HEAD")) { $inProgress += "merge  -> git merge --abort" }
    if (Test-Path -LiteralPath (Join-Path $gitDir "CHERRY_PICK_HEAD")) { $inProgress += "cherry-pick  -> git cherry-pick --abort" }
    if (Test-Path -LiteralPath (Join-Path $gitDir "REVERT_HEAD")) { $inProgress += "revert  -> git revert --abort" }
    if ((Test-Path -LiteralPath (Join-Path $gitDir "rebase-merge")) -or (Test-Path -LiteralPath (Join-Path $gitDir "rebase-apply"))) {
        $inProgress += "rebase  -> git rebase --abort"
    }
    if ($inProgress.Count -gt 0) {
        Write-List $inProgress
        throw "U ovom folderu je ostala nezavrsena git operacija (gore). Zavrsi je ili je prekini navedenom komandom, pa pokreni ponovo."
    }
    Write-Ok "git radi, nema zaostalih lock-ova ni nezavrsenih operacija"

    Write-Step "[2/8] Remote-ovi"
    $hack = Find-Remote $HackathonRepoPattern
    if (-not $hack) {
        throw "U ovom folderu nema remote-a za leksaas/a2-hackathon - verovatno si u pogresnom folderu (npr. u nasem radnom repou). Pokreni iz foldera a2-hackathon ili prosledi -RepoPath."
    }
    $ours = Find-Remote $OurRepoPattern
    if (-not $ours) {
        Write-Warn "Nema remote-a za nas repo ($OurRepoUrl)."
        if (Test-Git remote get-url $OurRemoteDefaultName) {
            throw "Remote '$OurRemoteDefaultName' vec postoji, ali ne pokazuje na nas repo. Proveri 'git remote -v'."
        }
        Confirm-OrStop "Da dodam remote '$OurRemoteDefaultName'?"
        Invoke-GitChecked remote add $OurRemoteDefaultName $OurRepoUrl | Out-Null
        $ours = $OurRemoteDefaultName
    }
    $originUrl = & git remote get-url origin 2>$null
    if ($LASTEXITCODE -eq 0 -and $originUrl -match $OurRepoPattern) {
        Write-Warn "Ovo izgleda kao vas radni folder (origin = nas repo), a ne deploy folder a2-hackathon."
        Confirm-OrStop "Svejedno nastaviti ovde?"
    }
    Write-Ok "hakaton repo = '$hack', nas repo = '$ours', ciljna grana = $TargetBranch"

    Write-Step "[3/8] Cist radni folder"
    $dirty = @(Invoke-GitChecked status --porcelain --untracked-files=no)
    if ($dirty.Count -gt 0) {
        Write-List $dirty
        throw ("U ovom folderu ima necommit-ovanih izmena (gore). Ovaj folder sluzi samo za slanje na robota - kod se menja u nasem repou. " +
            "Ako su izmene vazne, prenesi ih u nas repo. Ako nisu, obrisi ih sa: git reset --hard")
    }
    $current = & git symbolic-ref --quiet --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0) { $script:OriginalBranch = $current }
    Write-Ok "nema necommit-ovanih izmena"

    Write-Step "[4/8] Preuzimanje sa GitHub-a"
    Invoke-Fetch $hack $TargetBranch
    Invoke-Fetch $ours $SourceBranch
    $hackRef = "refs/remotes/$hack/$TargetBranch"
    $srcRef = "refs/remotes/$ours/$SourceBranch"
    foreach ($ref in @($hackRef, $srcRef)) {
        if (-not (Test-Git rev-parse --verify --quiet "$ref^{commit}")) { throw "Posle fetch-a ne postoji $ref. Proveri 'git remote -v' i ime grane." }
    }
    if (Test-Git merge-base --is-ancestor $srcRef $hackRef) {
        Write-Ok "$ours/$SourceBranch je vec ceo na $hack/$TargetBranch - nema sta da se salje."
        return
    }
    $newCommits = @(Invoke-GitChecked log --no-merges "--format=%h  %an, %ar: %s" "$hackRef..$srcRef")
    Write-Host "    Salje se $($newCommits.Count) commit-ova iz $ours/${SourceBranch}:"
    Write-List $newCommits 15
    Write-Host "    (Salje se ono sto je na GitHub-u, NE lokalne izmene. Ako nesto nije pushovano u nas repo, prekini, pushuj pa pokreni ponovo.)"
    if ($SourceBranch -ne "main") { Write-Warn "Saljes granu '$SourceBranch', a ne main." }

    Write-Step "[5/8] Tudje izmene na $TargetBranch"
    $syncPoint = Find-LastSyncPoint $hackRef $ours
    if ($syncPoint) { $range = @("$syncPoint..$hackRef") }
    else {
        Write-Warn "Nisam nasao prethodni sync - prikazujem sve commit-ove na grani koji nisu iz naseg repoa."
        $range = @($hackRef)
    }
    $foreign = @(Invoke-GitChecked rev-list --no-merges @range --not "--remotes=$ours")
    if ($foreign.Count -gt 0) {
        Write-Warn "Posle poslednjeg sync-a neko je direktno menjao $TargetBranch ($($foreign.Count) commit-ova):"
        Write-List @(Invoke-GitChecked log --no-merges --date=short "--format=%h  %an, %ad: %s" @range --not "--remotes=$ours")
        $foreignFiles = @(Invoke-GitChecked log --no-merges "--format=" --name-only @range --not "--remotes=$ours" | Where-Object { $_ } | Sort-Object -Unique)
        Write-Host "    Fajlovi koje su menjali:"
        Write-List $foreignFiles
        $base = & git merge-base $hackRef $srcRef 2>$null
        if ($LASTEXITCODE -eq 0) {
            $oursChanged = @{}
            foreach ($f in @(Invoke-GitChecked diff --name-only $base $srcRef)) { $oursChanged[$f] = $true }
            $overlap = @($foreignFiles | Where-Object { $oursChanged.ContainsKey($_) })
            if ($overlap.Count -gt 0) {
                Write-Warn "Ove fajlove smo menjali i mi i oni - ocekuj konflikt:"
                Write-List $overlap
            }
        }
        Write-Host "    Njihove izmene OSTAJU na grani. Ako je ovo drugi tim, javite im da rade na svojoj grani."
        Confirm-OrStop "Nastaviti?"
    }
    else {
        Write-Ok "niko drugi nije menjao granu od poslednjeg sync-a"
    }

    Write-Step "[6/8] Lokalna grana $TargetBranch"
    $localRef = "refs/heads/$TargetBranch"
    if (Test-Git show-ref --verify --quiet $localRef) {
        $localOnly = @(Invoke-GitChecked rev-list --no-merges $localRef "^$hackRef" --not "--remotes=$ours")
        if ($localOnly.Count -gt 0) {
            Write-List @(Invoke-GitChecked log --no-merges "--format=%h  %an, %ar: %s" $localRef "^$hackRef" --not "--remotes=$ours")
            throw ("Na lokalnoj grani $TargetBranch postoje commit-ovi kojih nema ni na GitHub-u ni u nasem repou (gore) - neko je commit-ovao direktno u ovom folderu. " +
                "Prenesi ih u nas repo (u radnom folderu: git cherry-pick <hash>), pa ih ovde odbaci sa: git checkout $TargetBranch; git reset --hard $hack/$TargetBranch")
        }
        $unpushed = @(Invoke-GitChecked rev-list "$hackRef..$localRef")
        if ($unpushed.Count -gt 0) {
            Write-Warn "Lokalno postoje nepushovani sync merge-ovi (verovatno od ranijeg prekinutog pokretanja) - odbacujem ih, prave se ponovo."
        }
    }
    Invoke-GitChecked checkout --quiet -B $TargetBranch $hackRef | Out-Null
    if ($script:OriginalBranch -ne $TargetBranch) { $script:SwitchedBranch = $true }

    $untracked = @(Invoke-GitChecked ls-files --others --exclude-standard)
    if ($untracked.Count -gt 0) {
        $incoming = @{}
        foreach ($f in @(Invoke-GitChecked diff --name-only --no-renames --diff-filter=A HEAD $srcRef)) { $incoming[$f] = $true }
        $clash = @($untracked | Where-Object { $incoming.ContainsKey($_) })
        if ($clash.Count -gt 0) {
            Write-List $clash
            throw "Ovi fajlovi postoje u folderu kao ne-pracene kopije, a nas repo ih donosi (gore). Obrisi ih ili premesti, pa pokreni ponovo."
        }
    }
    Write-Ok "lokalna grana = $hack/$TargetBranch"

    Write-Step "[7/8] Spajanje i provere"
    $env:GIT_MERGE_AUTOEDIT = "no"
    $script:MergeStarted = $true
    & git merge --no-ff --no-commit $srcRef
    if ($LASTEXITCODE -ne 0) {
        $conflicts = @(& git -c core.quotepath=false diff --name-only --diff-filter=U)
        & git merge --abort *> $null
        $script:MergeStarted = $false
        if ($conflicts.Count -eq 0) {
            throw "Git nije mogao da zapocne merge (poruka iznad). Folder je vracen u cisto stanje - nista nije pushovano."
        }
        Write-Host "    Konflikti:"
        Write-List $conflicts
        throw ("Merge konflikt (fajlovi gore). Merge je automatski prekinut i folder je vracen u cisto stanje - nista nije pushovano. " +
            "Najcesce neko je menjao iste fajlove na $TargetBranch (vidi korak 5). Ne razresavaj naslepo - prvo proveri cije su izmene.")
    }

    $changed = @(Invoke-GitChecked diff --cached --name-only --diff-filter=ACMR HEAD)
    $blocking = New-Object System.Collections.Generic.List[string]
    $warnings = New-Object System.Collections.Generic.List[string]
    $seen = @{}

    $file = $null
    $expectHeader = $false
    foreach ($line in @(Invoke-GitChecked diff --cached -U0 --no-color --no-ext-diff --diff-filter=ACMR HEAD)) {
        if ($line.StartsWith("diff --git ")) { $expectHeader = $true; continue }
        if ($expectHeader -and $line.StartsWith("+++ ")) {
            $file = $line.Substring(4) -replace '^b/', ''
            $expectHeader = $false
            continue
        }
        if ($expectHeader -or -not $line.StartsWith("+")) { continue }
        $text = $line.Substring(1)
        if ($text -match '^(<{7}|>{7})( |$)' -and -not $seen.ContainsKey("m:$file")) {
            $seen["m:$file"] = $true
            $blocking.Add("${file}: ostao konfliktni marker (<<<<<<< ili >>>>>>>)")
        }
        foreach ($name in $SecretPatterns.Keys) {
            if ($text -cmatch $SecretPatterns[$name] -and -not $seen.ContainsKey("s:${file}:$name")) {
                $seen["s:${file}:$name"] = $true
                $warnings.Add("${file}: izgleda kao tajna ($name)")
            }
        }
    }

    foreach ($f in $changed) {
        if ($f -match $SensitiveFilePattern -and $f -notmatch $SensitiveFileAllowPattern) {
            $warnings.Add("${f}: osetljiv fajl (.env / kljuc / lokalni config)")
        }
    }

    $tree = Invoke-GitChecked write-tree
    $sizes = @{}
    foreach ($line in @(Invoke-GitChecked ls-tree -r -l $tree)) {
        $tab = $line.IndexOf("`t")
        if ($tab -lt 0) { continue }
        $meta = $line.Substring(0, $tab) -split '\s+'
        if ($meta[1] -eq 'blob') { $sizes[$line.Substring($tab + 1)] = [int64]$meta[3] }
    }
    foreach ($f in $changed) {
        $size = $sizes[$f]
        if ($null -eq $size) { continue }
        $mb = [math]::Round($size / 1MB, 1)
        if ($size -gt $HardLimitBytes) { $blocking.Add("${f}: $mb MB - GitHub odbija fajlove preko 100 MB") }
        elseif ($size -gt $WarnLimitBytes) { $warnings.Add("${f}: veliki fajl ($mb MB)") }
    }

    $pyFiles = @($changed | Where-Object { $_ -like '*.py' })
    if ($pyFiles.Count -gt 0) {
        foreach ($problem in @(Test-PythonSyntax $pyFiles)) { $warnings.Add($problem) }
    }

    if ($blocking.Count -gt 0) {
        Write-Host "    Problemi koji blokiraju slanje:" -ForegroundColor Red
        Write-List $blocking
        if ($warnings.Count -gt 0) {
            Write-Warn "Uz to, proveri i ovo:"
            Write-List $warnings
        }
        throw "Popravi ovo u nasem repou, pushuj i pokreni ponovo. Nista nije pushovano."
    }
    if ($warnings.Count -gt 0) {
        Write-Warn "Proveri pre slanja - hakaton repo vide organizatori i drugi timovi:"
        Write-List $warnings
        Confirm-OrStop "Nastaviti uprkos upozorenjima?"
    }
    else {
        Write-Ok "$($changed.Count) izmenjenih fajlova: bez konflikata, markera, tajni, prevelikih fajlova i Python gresaka"
    }

    if ($DryRun) {
        & git merge --abort *> $null
        $script:MergeStarted = $false
        Write-Step "Probni rad (-DryRun): sve provere su prosle, nista nije commit-ovano ni pushovano."
        return
    }

    $srcShort = Invoke-GitChecked rev-parse --short $srcRef
    Invoke-GitChecked commit --quiet --no-edit -m "Sync $ours/$SourceBranch @ $srcShort for robot testing" | Out-Null
    $script:MergeStarted = $false

    Write-Step "[8/8] Push na $hack/$TargetBranch"
    Confirm-OrStop "Pushujem $($newCommits.Count) commit-ova iz $ours/$SourceBranch ($srcShort) na $hack/${TargetBranch}?"
    & git push $hack "refs/heads/${TargetBranch}:refs/heads/${TargetBranch}"
    if ($LASTEXITCODE -ne 0) {
        throw ("Push nije uspeo. Najcesce je neko u medjuvremenu pushovao na $TargetBranch - samo pokreni skriptu ponovo, ona ce to prikazati. " +
            "Ako pise 403 / Permission denied, proveri git login za github.com.")
    }
    Invoke-Fetch $hack $TargetBranch
    $head = Invoke-GitChecked rev-parse HEAD
    $remote = Invoke-GitChecked rev-parse $hackRef
    if ($head -ne $remote) { throw "Push je prosao, ali $hack/$TargetBranch sada pokazuje na drugi commit. Pokreni skriptu ponovo." }
    Write-Step "Gotovo: $ours/$SourceBranch ($srcShort) je na $hack/$TargetBranch"
    Write-Host "    https://github.com/leksaas/a2-hackathon/tree/$TargetBranch" -ForegroundColor Green
}

$exitCode = 0
$previousEncoding = $null
try { $previousEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
Push-Location
try {
    Invoke-Sync
}
catch {
    Write-Host ""
    Write-Host "STOP: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
}
finally {
    if ($script:MergeStarted) {
        & git merge --abort *> $null
        Write-Host "    Nezavrsen merge je prekinut - folder je vracen u stanje pre spajanja." -ForegroundColor Yellow
    }
    if ($script:SwitchedBranch -and $script:OriginalBranch) {
        & git checkout --quiet $script:OriginalBranch *> $null
    }
    Pop-Location
    if ($previousEncoding) { try { [Console]::OutputEncoding = $previousEncoding } catch { } }
}
exit $exitCode
