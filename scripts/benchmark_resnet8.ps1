param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string[]]$ArgumentList = @()
)

# The executable is expected to perform the firmware's 5 warmups + 20 timed
# iterations. Stopwatch measures host wall time around the complete command;
# target reports should use the adapter's timer ticks for inference-only time.
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$output = & $Executable @ArgumentList
$exitCode = $LASTEXITCODE
$sw.Stop()
$seconds = $sw.Elapsed.TotalSeconds
$output | ForEach-Object { $_ }
"elapsed_seconds={0:R}" -f $seconds
"exit_code={0}" -f $exitCode
if ($output -match 'tokens') {
    'token_metric=reported_by_executable'
} else {
    'token_metric=not_exposed'
}
exit $exitCode
