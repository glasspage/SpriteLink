[CmdletBinding()]
param(
    [Parameter()]
    [ValidatePattern('^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')]
    [string] $Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$versionInfoPath = Join-Path ([System.IO.Path]::GetTempPath()) (
    "spritelink-version-{0}.txt" -f $PID
)
$generatedModuleDirectory = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("spritelink-build-{0}" -f $PID)
$generatedVersionModulePath = Join-Path (
    $generatedModuleDirectory
) "spritelink_build_version.py"

$versionParts = @($Version.Split(".") | ForEach-Object { [int] $_ })
$versionTuple = @($versionParts[0], $versionParts[1], $versionParts[2], 0)
if ($versionTuple | Where-Object { $_ -gt 65535 }) {
    throw "Every version component must be 65535 or lower."
}
$versionTupleText = $versionTuple -join ", "

$versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($versionTupleText),
    prodvers=($versionTupleText),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'SpriteLink'),
          StringStruct('FileDescription', 'SpriteLink'),
          StringStruct('FileVersion', '$Version'),
          StringStruct('InternalName', 'SpriteLink'),
          StringStruct('OriginalFilename', 'SpriteLink.exe'),
          StringStruct('ProductName', 'SpriteLink'),
          StringStruct('ProductVersion', '$Version')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@

Push-Location $projectRoot
try {
    Set-Content -Path $versionInfoPath -Value $versionInfo -Encoding utf8
    $env:SPRITELINK_VERSION_FILE = $versionInfoPath
    New-Item -ItemType Directory -Force -Path (
        $generatedModuleDirectory
    ) | Out-Null
    Set-Content -Path $generatedVersionModulePath `
        -Value "VERSION = `"$Version`"" `
        -Encoding utf8
    $env:SPRITELINK_GENERATED_MODULE_DIRECTORY = (
        $generatedModuleDirectory
    )

    python -m PyInstaller --noconfirm --clean SpriteLink.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $isccCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($isccCommand) {
        $isccPath = $isccCommand.Source
    } else {
        $isccPath = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
    }
    if (-not (Test-Path $isccPath)) {
        throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php"
    }

    & $isccPath "/DAppVersion=$Version" "packaging\windows\SpriteLink.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }

    $installerPath = Join-Path $projectRoot (
        "dist\installer\SpriteLink-Setup-{0}.exe" -f $Version
    )
    if (-not (Test-Path $installerPath)) {
        throw "The expected installer was not created: $installerPath"
    }
    Write-Host "Created $installerPath"
} finally {
    Pop-Location
    Remove-Item Env:SPRITELINK_VERSION_FILE -ErrorAction SilentlyContinue
    Remove-Item Env:SPRITELINK_GENERATED_MODULE_DIRECTORY `
        -ErrorAction SilentlyContinue
    Remove-Item $versionInfoPath -ErrorAction SilentlyContinue
    Remove-Item $generatedModuleDirectory -Recurse -Force `
        -ErrorAction SilentlyContinue
}
