param(
    [string]$QuartusRoot = "C:\altera_pro\26.1\quartus",
    [string]$Feature = "6AF7_018B"
)

$ErrorActionPreference = "Stop"
$quartusParent = Split-Path -Parent $QuartusRoot
$quartusSh = Join-Path $QuartusRoot "bin64\quartus_sh.exe"
$lmutil = Join-Path $quartusParent "qcore\bin64\lmutil.exe"

foreach ($required in @($quartusSh, $lmutil)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Missing required Quartus tool: $required"
    }
}

$licenseSources = [Collections.Generic.List[string]]::new()
foreach ($variable in @("LM_LICENSE_FILE", "ALTERAD_LICENSE_FILE")) {
    $value = [Environment]::GetEnvironmentVariable($variable)
    if ($value) {
        foreach ($item in ($value -split ";")) {
            if ($item.Trim()) { $licenseSources.Add($item.Trim()) }
        }
    }
}

$quartusOption = (& $quartusSh --tcl_eval get_user_option -name LICENSE_FILE 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "quartus_sh could not read the configured LICENSE_FILE option"
}
foreach ($item in ($quartusOption -split ";")) {
    if ($item.Trim()) { $licenseSources.Add($item.Trim(' ', '"', "'")) }
}

$licenseSources = @($licenseSources | Select-Object -Unique)
if ($licenseSources.Count -eq 0) {
    throw "No Quartus license source is configured"
}

foreach ($source in $licenseSources) {
    $status = (& $lmutil lmstat -c $source -f $Feature 2>&1 | Out-String)
    if ($status -match [regex]::Escape("Users of $Feature")) {
        Write-Host "CoreDLA hardware license feature $Feature is available"
        exit 0
    }
}

throw "CoreDLA hardware license feature $Feature is unavailable. Do not generate or benchmark inference-limited IP. Checked $($licenseSources.Count) configured license source(s)."
