[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Find-Gcc {
    if ($env:FARCEL_GCC -and (Test-Path -LiteralPath $env:FARCEL_GCC)) { return $env:FARCEL_GCC }
    $command = Get-Command gcc -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $wingetGcc = "C:\Users\$env:USERNAME\AppData\Local\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.MCF.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\gcc.exe"
    if (Test-Path -LiteralPath $wingetGcc) { return $wingetGcc }
    throw "未找到 GCC。请将 gcc 放入 PATH，或设置 FARCEL_GCC。"
}

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$asset = Join-Path $repositoryRoot "src\farcel\standard_library\assets\math\product\Product.fmu"
$metadata = Join-Path $repositoryRoot "src\farcel\standard_library\assets\categories\math\product\block.json"
$stage = Join-Path ([System.IO.Path]::GetTempPath()) "farcel-standard-product-build"
$gcc = Find-Gcc

Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Join-Path $stage "binaries\win64") -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $asset) -Force | Out-Null

& $gcc -shared -std=c11 -O2 -s -static-libgcc -o (Join-Path $stage "binaries\win64\FarcelProduct.dll") (Join-Path $PSScriptRoot "source\product.c")
if ($LASTEXITCODE -ne 0) { throw "GCC 未能编译 Product.dll" }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "source\modelDescription.xml") -Destination (Join-Path $stage "modelDescription.xml")

$archive = "$asset.zip"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
Move-Item -LiteralPath $archive -Destination $asset -Force

$json = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
$json.fmu_sha256 = (Get-FileHash -LiteralPath $asset -Algorithm SHA256).Hash.ToLowerInvariant()
$json | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $metadata -Encoding utf8
Remove-Item -LiteralPath $stage -Recurse -Force
Write-Output "Built $asset"
Write-Output "SHA-256: $($json.fmu_sha256)"
