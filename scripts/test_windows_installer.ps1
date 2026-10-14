# Run only on a disposable Windows build runner.
$ErrorActionPreference = 'Stop'
$installers = @(Get-ChildItem -Path "$PSScriptRoot/../dist/*-windows-x64-setup.exe")
if ($installers.Count -ne 1) { throw 'Expected exactly one Windows setup executable' }
$target = Join-Path $env:RUNNER_TEMP 'ByAuthorX Installer Smoke'
if (!$env:RUNNER_TEMP -or (Test-Path -LiteralPath $target)) {
    throw 'A clean disposable RUNNER_TEMP directory is required'
}
$beforePath = [Environment]::GetEnvironmentVariable('Path', 'User')
$process = Start-Process -FilePath $installers[0].FullName -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$target`""
) -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Installation failed: $($process.ExitCode)" }
try {
    foreach ($name in @('ByAuthorX.exe', 'byax.exe', 'byax-mcp.exe', 'unins000.exe')) {
        if (!(Test-Path -LiteralPath (Join-Path $target $name))) { throw "Missing installed $name" }
    }
    & (Join-Path $target 'byax.exe') capabilities --json
    if ($LASTEXITCODE -ne 0) { throw 'Installed CLI failed' }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($target -notin ($userPath -split ';')) { throw 'Installer did not add user PATH' }
    $command = (Get-Item 'HKCU:\Software\Classes\ByAuthorX.Document\shell\open\command').GetValue('')
    if ($command -ne "`"$target\ByAuthorX.exe`" `"%1`"") { throw 'Invalid document open command' }
} finally {
    $uninstaller = Join-Path $target 'unins000.exe'
    $process = Start-Process -FilePath $uninstaller -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'
    ) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Uninstall failed: $($process.ExitCode)" }
}
if (Test-Path -LiteralPath (Join-Path $target 'ByAuthorX.exe')) { throw 'Uninstall left application executable' }
if ([Environment]::GetEnvironmentVariable('Path', 'User') -ne $beforePath) {
    throw 'Uninstall did not restore user PATH'
}
if (Test-Path 'HKCU:\Software\Classes\ByAuthorX.Document') { throw 'Uninstall left file association' }
Write-Host 'Windows install, installed CLI, association, PATH and uninstall checks passed.'
