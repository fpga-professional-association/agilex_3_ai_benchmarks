#!/usr/bin/env bash
# scripts/env.sh (#1) — sourceable environment for Quartus Prime Pro 26.1 (Agilex 3) + FPGA AI
# Suite 2026.1.1. Both ship as official Altera Docker images on this host rather than a native
# install; see docs/toolchain.md for the exact tags and why. This script defines shell functions
# that shadow the real tool names (quartus_sh, dla_compiler, ...) and transparently proxy each
# call into the right container, bind-mounting the repo so relative paths behave the same as a
# native install.
#
# Must be sourced, not executed:
#   source scripts/env.sh

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "scripts/env.sh must be sourced, not executed: source ${BASH_SOURCE[0]}" >&2
    exit 1
fi

_ENVSH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export QUARTUS_ROOTDIR=/opt/altera/quartus
export COREDLA_ROOT=/opt/altera/fpga_ai_suite/ubuntu/dla
export AI_SUITE_OPENVINO_DIR="${AI_SUITE_OPENVINO_DIR:-$HOME/openvino_2025.4.0}"

_envsh_container_pwd() {
    # Map the host $PWD onto its path inside the /workspace bind mount, so a tool run from
    # quartus/smoke/ on the host sees the same relative layout inside the container.
    printf '/workspace%s' "${PWD#"$_ENVSH_ROOT"}"
}

_envsh_run_quartus() {
    local tty=(); [ -t 1 ] && tty=(-t)
    # The -v /dev/null:.../libudev.so.1 mount below works around a crash that hits every
    # invocation of this container, on any design: FlexLM's license checkout (inside
    # libsys_cpt.so) does a VM/hypervisor fingerprinting probe that dlopen()s libudev.so.1
    # ("Search UDEV for QEMU/XEN/PARALLELS/...", per strings on libsys_cpt.so). On this
    # container's glibc that dlopen hits an RTLD_DEEPBIND-vs-TBB-allocator mismatch and
    # aborts with a heap-corruption `realloc(): invalid pointer` inside libudev's
    # udev_enumerate_scan_devices(), taking the whole Quartus process down mid-Fitter.
    # Bind-mounting /dev/null over libudev.so.1 makes that dlopen fail cleanly instead, so
    # FlexLM just skips the fingerprinting step and licensing proceeds normally. Confirmed
    # safe repo-wide: only libsys_cpt.so (licensing, needed) and libsld_filejni.so
    # (JTAG/SignalTap JNI, unused by this repo's headless synth/fit/sta/asm flows) reference
    # libudev anywhere in this Quartus install. See docs/toolchain.md, "known quirks" (a).
    docker run --rm -i "${tty[@]}" \
        --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$_ENVSH_ROOT:/workspace" \
        -v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1 \
        -w "$(_envsh_container_pwd)" \
        alterafpga/quartus-pro:26.1-agilex3 \
        "$@"
}

_envsh_run_ai_suite() {
    local tty=(); [ -t 1 ] && tty=(-t)
    if [ ! -f "$AI_SUITE_OPENVINO_DIR/setupvars.sh" ]; then
        echo "env.sh: \$AI_SUITE_OPENVINO_DIR ($AI_SUITE_OPENVINO_DIR) has no setupvars.sh" \
             "— extract openvino/ubuntu24_runtime:2025.4.0's /opt/intel/openvino there first" \
             "(docs/toolchain.md)." >&2
        return 1
    fi
    docker run --rm -i "${tty[@]}" \
        --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -v "$_ENVSH_ROOT:/workspace" \
        -v "$AI_SUITE_OPENVINO_DIR:/opt/intel/openvino:ro" \
        -w "$(_envsh_container_pwd)" \
        alterafpga/fpgaaisuite:2026.1.1-quartus \
        bash -c '
            # setupvars.sh does a bare tcsh-compat `set tcsh_msg=...`, which — because this is
            # sourced, not run in a subshell — clobbers "$@" as a side effect. Stash the real
            # command in an array first (docs/toolchain.md "known quirks").
            CMD=("$@")
            source /opt/intel/openvino/setupvars.sh >/dev/null
            source /opt/altera/fpga_ai_suite/ubuntu/dla/setupvars.sh >/dev/null
            exec "${CMD[@]}"
        ' bash "$@"
}

quartus_sh()    { _envsh_run_quartus quartus_sh "$@"; }
quartus_syn()   { _envsh_run_quartus quartus_syn "$@"; }
quartus_fit()   { _envsh_run_quartus quartus_fit "$@"; }
quartus_asm()   { _envsh_run_quartus quartus_asm "$@"; }
quartus_sta()   { _envsh_run_quartus quartus_sta "$@"; }
quartus_pgm()   { _envsh_run_quartus quartus_pgm "$@"; }
qsys-generate() { _envsh_run_quartus qsys-generate "$@"; }
qsys-script()   { _envsh_run_quartus qsys-script "$@"; }

dla_compiler()                { _envsh_run_ai_suite dla_compiler "$@"; }
dlac()                         { _envsh_run_ai_suite dlac "$@"; }
dla_create_ip()                { _envsh_run_ai_suite dla_create_ip "$@"; }
dla_benchmark()                { _envsh_run_ai_suite dla_benchmark "$@"; }
dla_build_example_design.py()  { _envsh_run_ai_suite dla_build_example_design.py "$@"; }

echo "[env.sh] Quartus Prime Pro 26.1.0 Build 110 (agilex3) + FPGA AI Suite 2026.1.1 ready" \
     "(Docker-backed, see docs/toolchain.md)." >&2
