# Build wheel (Windows)
# Run from repo root: powershell -File scripts/wheel_build.ps1
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$DashboardDir = Join-Path $RepoRoot "dashboard"
$DashboardDest = Join-Path $RepoRoot "src\octop\dashboard"

Write-Host "[wheel_build] Building dashboard frontend..."
Push-Location $DashboardDir
npm ci
npm run build
Pop-Location

$indexHtml = Join-Path $DashboardDest "index.html"
if (-not (Test-Path $indexHtml)) {
    throw "Dashboard build did not produce src/octop/dashboard/index.html"
}

$bash = Get-Command bash -ErrorAction SilentlyContinue
if ($null -eq $bash) {
    throw "PLAT brand patch requires bash (Git for Windows). Install bash and rerun; refusing to build an unbranded wheel."
}
Write-Host "[wheel_build] Applying PLAT brand..."
$RepoRootForBash = $RepoRoot -replace "\\", "/"
& $bash.Source (Join-Path $RepoRoot "brand/patch-octop-brand.sh") $RepoRootForBash
if ($LASTEXITCODE -ne 0) {
    throw "PLAT brand patch failed with exit code $LASTEXITCODE"
}
Write-Host "[wheel_build] Verifying PLAT brand..."
python (Join-Path $RepoRoot "brand\verify-octop-brand.py") --directory $RepoRoot

Write-Host "[wheel_build] Dashboard ready at src/octop/dashboard/"

Write-Host "[wheel_build] Building wheel + sdist..."
python -m pip install --quiet build
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
python -m build --outdir dist .

$OctopWheel = Get-ChildItem (Join-Path $RepoRoot "dist") -Filter "octop-*.whl" | Select-Object -First 1
python (Join-Path $RepoRoot "brand\verify-octop-brand.py") --artifact $OctopWheel.FullName

Write-Host "[wheel_build] Done. Artifacts in: $RepoRoot\dist\"
Get-ChildItem dist
