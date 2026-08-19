[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$thirdParty = Join-Path $root 'third_party'
New-Item -ItemType Directory -Force -Path $thirdParty | Out-Null

function Clone-AtCommit([string]$url, [string]$destination, [string]$commit) {
    if (Test-Path $destination) { throw "Destination already exists: $destination" }
    git clone --no-checkout $url $destination
    if ($LASTEXITCODE -ne 0) { throw "git clone failed for $url" }
    git -C $destination checkout --detach $commit
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed for $commit" }
    $head = (git -C $destination rev-parse HEAD).Trim()
    if ($head -ne $commit) { throw "Unexpected HEAD $head (wanted $commit)" }
}

Clone-AtCommit 'https://github.com/ArrowElectronics/refdes-agilex3.git' (Join-Path $thirdParty 'arrow_refdes') '62c062464531a4c1f7cfa9b18b6f73fa4b41f6d3'
Clone-AtCommit 'https://github.com/mlcommons/tiny.git' (Join-Path $thirdParty 'mlcommons_tiny') '4addd0fa08d216e20637637874e084895f289da4'
$model = Join-Path $thirdParty 'mlcommons_tiny\benchmark\training\image_classification\trained_models\pretrainedResnet_quant.tflite'
$modelHash = (Get-FileHash -Algorithm SHA256 $model).Hash
if ($modelHash -ne '3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415') { throw "Unexpected model SHA-256 $modelHash" }

$schematicRepo = Join-Path ([System.IO.Path]::GetTempPath()) ('agilex3-schematic-' + [guid]::NewGuid().ToString('N'))
try {
    git clone --filter=blob:none --no-checkout https://github.com/ArrowElectronics/Agilex-3.git $schematicRepo
    if ($LASTEXITCODE -ne 0) { throw 'git clone failed for Agilex-3' }
    git -C $schematicRepo sparse-checkout init --no-cone
    git -C $schematicRepo sparse-checkout set --no-cone 'images/AXC3000/SCH-TEI0131-01-P001.PDF'
    git -C $schematicRepo checkout --detach 0578b8c6ced7f0f006318e021ee7773608d83465
    if ($LASTEXITCODE -ne 0) { throw 'git checkout failed for Agilex-3 schematic' }
    $source = Join-Path $schematicRepo 'images\AXC3000\SCH-TEI0131-01-P001.PDF'
    if (-not (Test-Path $source)) { throw "Sparse checkout did not produce $source" }
    $outDir = Join-Path $thirdParty 'arrow_refdes_schematic'
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $destination = Join-Path $outDir 'SCH-TEI0131-01-P001.PDF'
    Copy-Item $source $destination
    $blob = (git -C $schematicRepo hash-object $source).Trim()
    if ($blob -ne '467cf7913bc3fcdafcbba9355421f67e224e48ac') { throw "Unexpected schematic blob $blob" }
    $schematicHash = (Get-FileHash -Algorithm SHA256 $destination).Hash
    if ($schematicHash -ne 'A3548E1B1E61498A71791C531D3D9B68844106842BC9E51FAAF5E2BA826873A9') { throw "Unexpected schematic SHA-256 $schematicHash" }
}
finally {
    if (Test-Path -LiteralPath $schematicRepo) {
        $resolvedRepo = (Resolve-Path -LiteralPath $schematicRepo -ErrorAction Stop).Path
        $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        $tempPrefix = $tempRoot.TrimEnd('\','/') + [System.IO.Path]::DirectorySeparatorChar
        $resolvedFull = [System.IO.Path]::GetFullPath($resolvedRepo)
        if (-not $resolvedFull.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to recursively delete non-temporary path: $resolvedFull"
        }
        Remove-Item -LiteralPath $resolvedFull -Recurse -Force
    }
}
Write-Host 'Sources fetched and pinned successfully.'
