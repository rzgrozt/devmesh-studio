param(
    [ValidateSet('install','upgrade','uninstall','purge','build')]
    [string]$Action = 'install',
    [string]$Source = '',
    [switch]$NoDesktop
)

$ErrorActionPreference = 'Stop'
$RepoUrl = if ($env:DEVMESH_REPO_URL) { $env:DEVMESH_REPO_URL } else { 'https://github.com/rzgrozt/devmesh-studio.git' }
$Branch = if ($env:DEVMESH_BRANCH) { $env:DEVMESH_BRANCH } else { 'main' }
$LocalAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$InstallRoot = if ($env:DEVMESH_INSTALL_DIR) { $env:DEVMESH_INSTALL_DIR } else { Join-Path $LocalAppData 'Programs\DevMesh Studio' }
$CheckoutRoot = Join-Path $LocalAppData 'DevMesh Studio\Source'

function Write-DevMesh([string]$Message) {
    Write-Host "[DevMesh] $Message"
}

function Get-Python {
    $candidates = @(
        @{ Command = 'py'; Prefix = @('-3.11') },
        @{ Command = 'python'; Prefix = @() },
        @{ Command = 'python3'; Prefix = @() }
    )
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate.Command -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $prefix = $candidate.Prefix
        try {
            & $cmd.Source @prefix -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
            if ($LASTEXITCODE -eq 0) {
                return @{ Command = $cmd.Source; Prefix = $prefix }
            }
        } catch {}
    }
    throw 'Python 3.11 or newer is required.'
}

function Invoke-Python($Python, [string[]]$Arguments, [string]$WorkingDirectory = '') {
    if ($WorkingDirectory) { Push-Location $WorkingDirectory }
    try {
        $allArgs = @()
        $allArgs += @($Python.Prefix)
        $allArgs += @($Arguments)
        & $Python.Command @allArgs
        if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
    } finally {
        if ($WorkingDirectory) { Pop-Location }
    }
}

$Python = Get-Python

if ($Action -eq 'uninstall' -or $Action -eq 'purge') {
    $manager = Join-Path $InstallRoot 'scripts\bootstrap.py'
    if (Test-Path $manager) {
        $args = @($manager, 'uninstall')
        if ($Action -eq 'purge') { $args += '--purge' }
        Invoke-Python $Python $args
        exit 0
    }
    Write-DevMesh "DevMesh is not installed at $InstallRoot"
    exit 0
}

if ($Action -eq 'upgrade') {
    $manager = Join-Path $InstallRoot 'scripts\bootstrap.py'
    if (-not (Test-Path $manager)) { throw "DevMesh is not installed at $InstallRoot" }
    $args = @($manager, 'upgrade')
    if ($Source) { $args += @('--source', $Source) }
    Invoke-Python $Python $args
    exit 0
}

# Prefer the source checkout containing this script when available.
if (-not $Source -and $PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'pyproject.toml'))) {
    $Source = $PSScriptRoot
}

if (-not $Source) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { throw 'Git is required for the one-line Windows installer.' }
    if (Test-Path (Join-Path $CheckoutRoot '.git')) {
        Write-DevMesh 'Refreshing source checkout'
        & $git.Source -C $CheckoutRoot fetch --depth 1 origin $Branch
        & $git.Source -C $CheckoutRoot checkout -q $Branch
        & $git.Source -C $CheckoutRoot reset --hard "origin/$Branch"
    } elseif (Test-Path $CheckoutRoot) {
        Remove-Item -LiteralPath $CheckoutRoot -Recurse -Force
        & $git.Source clone --depth 1 --branch $Branch $RepoUrl $CheckoutRoot
    } else {
        New-Item -ItemType Directory -Force -Path (Split-Path $CheckoutRoot) | Out-Null
        & $git.Source clone --depth 1 --branch $Branch $RepoUrl $CheckoutRoot
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not obtain DevMesh source.' }
    $Source = $CheckoutRoot
}

$bootstrap = Join-Path $Source 'scripts\bootstrap.py'
if (-not (Test-Path $bootstrap)) { throw "Invalid DevMesh source checkout: $Source" }

if ($Action -eq 'build') {
    Write-DevMesh 'Windows detected: building native executables'
    Invoke-Python $Python @($bootstrap, 'build-windows', '--source', $Source) $Source
    exit 0
}

Write-DevMesh 'Windows detected: building and installing native executables'
$args = @($bootstrap, 'install', '--source', $Source)
if ($NoDesktop) { $args += '--no-desktop' }
Invoke-Python $Python $args $Source
Write-DevMesh 'Installation complete.'
