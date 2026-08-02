<#
.SYNOPSIS
    Installs a3rig as a user-level command, from scratch.

.DESCRIPTION
    Finds a real Python 3.11+, bootstraps pipx if needed, puts pipx and its apps on the
    user PATH, and installs a3rig in editable mode.

    Safe to re-run: it reinstalls over any existing copy.

    An activated virtualenv is deliberately ignored. `py` and `python` both resolve to the
    venv's interpreter when one is active, which is what makes the manual steps go wrong -
    this script always calls a system interpreter by full path instead.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

function Write-Step { param([string]$Text) Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "    $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "    $Text" -ForegroundColor Yellow }

function Find-SystemPython {
    <#
        Returns the path to a Python 3.11+ interpreter that is not a virtualenv.
        `py -0p` lists interpreters registered with the launcher, which is unaffected by
        an active virtualenv, so it is the reliable source here.
    #>
    $candidates = New-Object System.Collections.Generic.List[string]

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($line in (py -0p 2>$null)) {
            $match = [regex]::Match($line, '(?<path>[A-Za-z]:\\.*python\.exe)\s*$')
            if ($match.Success) { $candidates.Add($match.Groups['path'].Value) }
        }
    }
    foreach ($version in '3.13', '3.12', '3.11') {
        $candidates.Add("$env:LOCALAPPDATA\Programs\Python\Python$($version -replace '\.','')\python.exe")
        $candidates.Add("$env:ProgramFiles\Python$($version -replace '\.','')\python.exe")
    }

    foreach ($candidate in $candidates) {
        if (-not (Test-Path $candidate)) { continue }
        # WindowsApps entries are Store stubs or aliases; they cannot host a --user install.
        if ($candidate -like '*\WindowsApps\*') { continue }
        # Kept to one line with no inner quotes: PowerShell mangles multi-line arguments
        # passed to a native executable.
        $probe = 'import sys; print(sys.version_info[0], sys.version_info[1], sys.prefix != sys.base_prefix)'
        $info = & $candidate -c $probe 2>$null
        if (-not $info) { continue }
        $major, $minor, $isVenv = $info.Trim() -split '\s+'
        if ($isVenv -eq 'True') { continue }
        $version = "$major.$minor"
        if ([version]$version -lt [version]'3.11') { continue }
        return @{ Path = $candidate; Version = $version }
    }
    return $null
}

function Get-PipxBinDir {
    <#
        Where pipx puts the app shims. Asked of pipx rather than assumed, so a
        non-default PIPX_BIN_DIR still produces a correct profile entry.
    #>
    param([string]$Python)
    $reported = & $Python -m pipx environment --value PIPX_BIN_DIR 2>$null
    if ($LASTEXITCODE -eq 0 -and $reported) { return $reported.Trim() }
    return (Join-Path $env:USERPROFILE '.local\bin')
}

function Add-PathToPowerShellProfile {
    <#
        Puts $BinDir on PATH from the PowerShell profile.

        `pipx ensurepath` only edits the persisted user PATH, which a process inherits
        once at launch. VS Code hands its terminals the environment it captured when it
        started, so a terminal opened in a long-running VS Code window never sees the new
        entry. A profile runs per-session regardless of what the parent handed down, which
        is what makes a new terminal enough.

        The block is delimited so re-running the installer rewrites it instead of
        appending a second copy.
    #>
    param([string]$BinDir)

    $start = '# >>> a3rig >>>'
    $end = '# <<< a3rig <<<'
    $block = @"
$start
# Added by a3rig's installer. Puts pipx's app directory on PATH for every new PowerShell
# session, so a3rig works in terminals started by a program (VS Code, for one) whose own
# environment predates the install.
`$a3rigBin = '$BinDir'
if ((Test-Path `$a3rigBin) -and ((`$env:PATH -split ';') -notcontains `$a3rigBin)) {
    `$env:PATH = "`$env:PATH;`$a3rigBin"
}
$end
"@

    # CurrentUserAllHosts: applies to the console host and to VS Code's terminal host.
    $profiles = @($PROFILE.CurrentUserAllHosts)
    if (Get-Command pwsh -ErrorAction SilentlyContinue) {
        $pwshProfile = & pwsh -NoProfile -Command '$PROFILE.CurrentUserAllHosts' 2>$null
        if ($pwshProfile) { $profiles += $pwshProfile.Trim() }
    }

    foreach ($path in $profiles) {
        $parent = Split-Path -Parent $path
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }

        if (Test-Path $path) {
            $existing = [IO.File]::ReadAllText($path)
            if ($existing -match [regex]::Escape($start)) {
                $pattern = "(?s)" + [regex]::Escape($start) + ".*?" + [regex]::Escape($end)
                $updated = [regex]::Replace($existing, $pattern, $block.Replace('$', '$$'))
                [IO.File]::WriteAllText($path, $updated)
                Write-Ok "updated $path"
                continue
            }
            $separator = if ($existing.EndsWith("`n") -or $existing.Length -eq 0) { '' } else { "`r`n" }
            [IO.File]::WriteAllText($path, $existing + $separator + "`r`n" + $block + "`r`n")
            Write-Ok "appended to $path"
        }
        else {
            [IO.File]::WriteAllText($path, $block + "`r`n")
            Write-Ok "created $path"
        }
    }
}

# An active virtualenv confuses pip and pipx even when the interpreter is given by full
# path, so it is hidden for the duration and restored on the way out.
$savedVirtualEnv = $env:VIRTUAL_ENV
try {
    $env:VIRTUAL_ENV = $null

    Write-Step 'Locating a system Python 3.11+'
    $python = Find-SystemPython
    if (-not $python) {
        Write-Host ''
        Write-Host 'No system Python 3.11 or newer was found.' -ForegroundColor Red
        Write-Host 'Install one with:  winget install --id Python.Python.3.12' -ForegroundColor Red
        exit 1
    }
    Write-Ok "Python $($python.Version) at $($python.Path)"
    $exe = $python.Path

    Write-Step 'Checking for pipx'
    & $exe -m pipx --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn 'not installed - installing it now'
        & $exe -m pip install --user --quiet --disable-pip-version-check pipx
        if ($LASTEXITCODE -ne 0) { throw 'pip could not install pipx' }
        Write-Ok 'pipx installed'
    }
    else {
        Write-Ok "pipx $((& $exe -m pipx --version 2>$null).Trim())"
    }

    Write-Step 'Adding pipx and its apps to your PATH'
    & $exe -m pipx ensurepath *> $null
    Write-Ok 'done'

    Write-Step "Installing a3rig from $projectRoot"
    & $exe -m pipx install --editable $projectRoot --force
    if ($LASTEXITCODE -ne 0) { throw 'pipx could not install a3rig' }

    $appDir = Get-PipxBinDir -Python $exe

    Write-Step 'Making it available in new terminals without a restart'
    Add-PathToPowerShellProfile -BinDir $appDir

    $a3rig = Join-Path $appDir 'a3rig.exe'
    Write-Host ''
    if (Test-Path $a3rig) {
        Write-Host "a3rig installed: $(& $a3rig --version)" -ForegroundColor Green

        # pipx warns about PATH using this process's stale copy. Check the persisted user
        # PATH instead, which is what a new terminal will actually see.
        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if ($userPath -split ';' -contains $appDir) {
            Write-Ok "$appDir is on your user PATH (ignore any PATH warning above)"
        }
        else {
            Write-Warn "$appDir is NOT on your user PATH - add it manually, or call $a3rig directly"
        }
        Write-Host ''
        Write-Host 'Open a NEW terminal - that is enough, including inside a VS Code window' -ForegroundColor White
        Write-Host 'that was already running. Then, from any HEMTT project:' -ForegroundColor White
        Write-Host ''
        Write-Host '    a3rig doctor      ' -NoNewline -ForegroundColor Cyan
        Write-Host '# check everything resolves'
        Write-Host '    a3rig --dry-run   ' -NoNewline -ForegroundColor Cyan
        Write-Host '# print the commands without starting anything'
        Write-Host '    a3rig             ' -NoNewline -ForegroundColor Cyan
        Write-Host '# build and start the rig'
        Write-Host ''
        Write-Host 'Set the three [server] paths before the server can start:' -ForegroundColor White
        Write-Host '    a3rig config --edit' -ForegroundColor Cyan
        Write-Host ''
        Write-Host 'The profile entry covers PowerShell. A cmd.exe terminal inside an' -ForegroundColor DarkGray
        Write-Host 'already-running VS Code still needs VS Code fully restarted.' -ForegroundColor DarkGray
    }
    else {
        Write-Warn "installed, but a3rig.exe was not found in $appDir - run ``$exe -m pipx list``"
    }
}
finally {
    $env:VIRTUAL_ENV = $savedVirtualEnv
}
