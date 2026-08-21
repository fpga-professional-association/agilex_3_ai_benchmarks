# Evaluation correction run profile

Date: 2026-08-21 (America/Chicago)

## Token telemetry

The Codex runtime did not expose per-agent or per-task token counters to the
workspace. No token counts are estimated. The auditable token field for the
root agent and all delegated tasks is therefore `token_metric=not_exposed`.

## Delegation

- GPT-5.6 SOL planned and verified the Platform Designer regeneration and
  detected the duplicate stale `_0` AI-IP project assignment.
- Lower-capability agents audited evaluation-license semantics and the CoreDLA
  streaming/profiling datapath.
- Agent wall-clock and token telemetry were not exposed by the collaboration
  interface.

## Measured command time

| Task | Wall seconds | Result |
|---|---:|---|
| Rejected absolute-path `qsys-script` probe | 0.2449977 | Failed before mutation; identified Windows path-form requirement |
| Platform Designer system script | 15.9391927 | Pass |
| Platform Designer synthesis generation | 19.2566450 | Pass |
| Mixed-provenance diagnostic Quartus compile | 316.8100638 | Pass; rejected as final evidence because stale and current IP descriptors were both listed |
| Clean single-source Quartus compile | 317.4015293 | Pass; 0 errors, timing met |
| JTAG scan | 0.1387035 | Pass; one AXC3000 detected |
| Program clean SOF | 4.7872612 | Pass; 0 errors, 0 warnings |
| Four-input firmware build | 11.4948772 | Pass |
| Four-input firmware download | 4.0990427 | Pass |
| Four-input UART capture | 4.7400304 | Target completed; host terminal emitted its known console-restore error afterward |
| Counter firmware build | 11.7365454 | Pass |
| Counter firmware download | 4.1379565 | Pass |
| Counter UART capture | 4.4496735 | Target completed |
| Corrected-profiler firmware build | 11.6260204 | Pass |
| Corrected-profiler firmware download | 4.0452367 | Pass |
| Corrected-profiler UART capture | 4.5794443 | Target completed; host terminal emitted its known console-restore error afterward |

The sum of directly instrumented commands above is `735.4872203` seconds.
This excludes reasoning, file review, report editing, delegation, and any prior
session work for which a stopwatch value was not captured.
