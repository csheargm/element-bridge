# Hagency Rooms: connect your own Codex or Claude Code as your agent, from this Windows computer.
#   & ([scriptblock]::Create((irm __HAGENCY_CONSOLE__/connect.ps1))) -Code CODE [-Agent codex|claude]
# (irm ... | iex can't pass the code, hence the scriptblock.) Installs or updates Hagency Rooms in
# %LOCALAPPDATA%\Hagency\app, has connect.py trade @pm's one-time code for your agent's sign-in
# (kept in %USERPROFILE%\.hagency\credentials.json, readable only by you), then registers the
# Scheduled Task "Hagency agent <name>": it runs connect_win.py at every logon, which keeps your
# agent running in the background. Run the line again with a new code to reconnect or update.
param(
    [Parameter(Mandatory = $true)][string]$Code,
    [ValidateSet('codex', 'claude')][string]$Agent
)
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$Console = if ($env:HAGENCY_CONSOLE) { $env:HAGENCY_CONSOLE } else { '__HAGENCY_CONSOLE__' }
$Repo = if ($env:HAGENCY_REPO) { $env:HAGENCY_REPO } else { 'https://github.com/csheargm/hagency-rooms' }
$App = if ($env:HAGENCY_APP) { $env:HAGENCY_APP } else { Join-Path $env:LOCALAPPDATA 'Hagency\app' }
$HagencyHome = Join-Path $env:USERPROFILE '.hagency'
$CodePattern = '^[A-Za-z0-9-]{4,32}$'          # what pairing.py codes look like; nothing else reaches python

function Say([string]$Text) { Write-Host $Text }

function Find-Python {
    # The newest Python 3.11+ (the py launcher first), as a full path; '' when there is none.
    $ErrorActionPreference = 'Continue'          # a missing version writes to stderr: not an error here
    $tries = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11') { $tries += , @('py', "-$v") }
    }
    $tries += , @('python')
    $tries += , @('python3')
    foreach ($try in $tries) {
        if (-not (Get-Command $try[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($try | Select-Object -Skip 1)
        $found = & $try[0] @rest -c 'import sys; sys.version_info >= (3, 11) and print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return ([string]@($found)[0]).Trim() }
    }
    return ''
}

function Invoke-Checked([string]$What, [scriptblock]$Run) {
    & $Run | Out-Host                            # shown, never part of a function's return value
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

function Stop-OldAgent {
    # Before updating: stop the running agent, so its files and Python aren't in use.
    Get-ScheduledTask -TaskName 'Hagency agent *' -ErrorAction SilentlyContinue |
        Stop-ScheduledTask -ErrorAction SilentlyContinue
    $pidFile = Join-Path $HagencyHome 'agent.pid'
    if (-not (Test-Path $pidFile)) { return }
    foreach ($line in Get-Content $pidFile) {
        $id = 0
        if (-not [int]::TryParse($line.Trim(), [ref]$id)) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
        # Only our own agent: a stale number may belong to something else by now.
        if ($proc -and $proc.CommandLine -and $proc.CommandLine.IndexOf($App, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Install-App {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($env:HAGENCY_APP) { Say "Using $App"; return }
    if ((Test-Path (Join-Path $App '.git')) -and $git) {
        Say "Updating $App"
        Invoke-Checked 'git pull' { git -C $App pull -q --ff-only }
        return
    }
    if (-not (Test-Path $App) -and $git) {
        Say "Installing Hagency Rooms in $App"
        New-Item -ItemType Directory -Force -Path (Split-Path $App) | Out-Null
        Invoke-Checked 'git clone' { git clone -q --depth 1 $Repo $App }
        return
    }
    Say "Downloading Hagency Rooms into $App (no git here)"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('hagency-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        $zip = Join-Path $tmp 'app.zip'
        Invoke-WebRequest -UseBasicParsing -Uri "$Repo/archive/refs/heads/master.zip" -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x')
        $src = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
        if (-not $src) { throw 'the download was empty' }
        New-Item -ItemType Directory -Force -Path $App | Out-Null
        Copy-Item -Path (Join-Path $src.FullName '*') -Destination $App -Recurse -Force
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

function Install-Venv([string]$Python) {
    $venvPy = Join-Path $App '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) {
        $ok = & $venvPy -c 'import sys; print(sys.version_info >= (3, 11))'
        if ("$ok".Trim() -ne 'True') { Invoke-Checked 'rebuilding the venv' { & $Python -m venv --clear (Join-Path $App '.venv') } }
    } else {
        Invoke-Checked 'creating the venv' { & $Python -m venv (Join-Path $App '.venv') }
    }
    Invoke-Checked 'installing requirements' {
        & $venvPy -m pip install -q --disable-pip-version-check -r (Join-Path $App 'requirements.txt')
    }
    return $venvPy
}

function Select-Backend {
    if ($Agent) { return $Agent }
    $found = @('codex', 'claude' | Where-Object { Get-Command $_ -ErrorAction SilentlyContinue })
    if ($found.Count -eq 1) { return $found[0] }
    if ($found.Count -eq 0) {
        throw ("install Codex (npm i -g @openai/codex) or Claude Code (irm https://claude.ai/install.ps1 | iex) " +
               'first, then open a new PowerShell and run this line again')
    }
    do {
        $choice = (Read-Host 'Which one should be your agent, codex or claude?').Trim().ToLower()
    } until ($choice -eq 'codex' -or $choice -eq 'claude')
    return $choice
}

function Protect-Home {
    # Only you (not other accounts on this PC) can read the folder that holds your agent's sign-in.
    New-Item -ItemType Directory -Force -Path $HagencyHome | Out-Null
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    Invoke-Checked 'protecting the sign-in folder' { icacls $HagencyHome /inheritance:r /grant:r "*${me}:(OI)(CI)F" /q | Out-Null }
}

function Register-Agent([string]$VenvPy) {
    $pyw = Join-Path $App '.venv\Scripts\pythonw.exe'
    $runner = Join-Path $App 'connect_win.py'
    $name = ([string](& $VenvPy $runner --task-name)).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $name) { throw 'could not read the saved sign-in' }
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    try {
        $action = New-ScheduledTaskAction -Execute $pyw -Argument ('"{0}"' -f $runner) -WorkingDirectory $App
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
        Start-ScheduledTask -TaskName $name
        Remove-Item (Join-Path ([Environment]::GetFolderPath('Startup')) "$name.lnk") -ErrorAction SilentlyContinue
        Say "Your agent runs in the background now, and again each time you sign in (task: $name)."
    } catch {
        # Some PCs don't let people make tasks: start it from the Startup folder instead.
        $link = Join-Path ([Environment]::GetFolderPath('Startup')) "$name.lnk"
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
        $shortcut.TargetPath = $pyw
        $shortcut.Arguments = '"{0}"' -f $runner
        $shortcut.WorkingDirectory = $App
        $shortcut.Save()
        Start-Process -FilePath $pyw -ArgumentList ('"{0}"' -f $runner) -WorkingDirectory $App -WindowStyle Hidden
        Say "Your agent runs in the background now, and again each time you sign in (Startup: $name)."
    }
    Say "Its log: $(Join-Path $HagencyHome 'agent.log'). You can close this window."
}

try {
    if ($Code -notmatch $CodePattern) { throw 'that does not look like a code from @pm (like ABCDE-FGHJK)' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $python = Find-Python
    if (-not $python) {
        throw 'needs Python 3.11 or newer: winget install -e --id Python.Python.3.12, then open a new PowerShell'
    }
    $backend = Select-Backend                    # before the code is used up
    Stop-OldAgent
    Install-App
    $venvPy = Install-Venv $python
    Protect-Home
    Invoke-Checked 'connecting' { & $venvPy (Join-Path $App 'connect.py') --server $Console $Code --agent $backend --pair-only }
    Register-Agent $venvPy
} catch {
    # Not `exit`: this runs inside the person's own PowerShell window, which must stay open.
    Write-Host "x $($_.Exception.Message)" -ForegroundColor Red
}
