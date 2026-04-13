param(
    [string]$AppName = "RegistroAsistenciaEscolar",
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "[1/4] Instalando dependencias de build..."
python -m pip install --upgrade pip pyinstaller

Write-Host "[2/4] Limpiando builds anteriores..."
if (Test-Path "$root\build") { Remove-Item "$root\build" -Recurse -Force }
if (Test-Path "$root\dist") { Remove-Item "$root\dist" -Recurse -Force }
if (Test-Path "$root\installer\output") { Remove-Item "$root\installer\output" -Recurse -Force }

Write-Host "[3/4] Compilando ejecutable..."
python -m PyInstaller --noconfirm --clean "$root\RegistroAsistenciaEscolar.spec"

$exePath = "$root\dist\$AppName\$AppName.exe"
if (-not (Test-Path $exePath)) {
    throw "No se encontro el EXE compilado en: $exePath"
}

Write-Host "[4/4] Compilando instalador Inno Setup..."
$possibleIscc = @(
    (Get-Command ISCC -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

if (-not $possibleIscc) {
    Write-Warning "Inno Setup no esta instalado. EXE generado en: $exePath"
    Write-Host "Instale Inno Setup 6 y ejecute: ISCC installer\setup.iss"
    exit 0
}

& $possibleIscc "$root\installer\setup.iss"
Write-Host "Instalador generado en: $root\installer\output"
