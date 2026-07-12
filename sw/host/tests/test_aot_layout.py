"""Unit tests for sw/host/aot_layout.py -- the .aot -> HyperRAM memory-layout resolver.

All four samples below are REAL `ddr_buffer_info_<subgraph>_0.txt` content, captured by re-running
the exact Track M `dla_compiler` command (`quartus/coredla_hyperram_ed/ip/README.md` §2) against
each MLPerf Tiny model's committed IR (`quartus/coredla_hyperram_ed/ip/models/<model>/<model>.xml`)
+ `models/arch/AGX3_Performance.arch` -- not invented text. See docs/coredla_inference_driver.md.

Pure-logic tests: no board, no Docker, no OpenVINO.
"""

import pytest

from aot_layout import (
    DEFAULT_ALIGN_BYTES,
    DEFAULT_GUARD_BYTES,
    build_inference_job,
    parse_ddr_buffer_info,
    resolve_hyperram_layout,
)

RESNET8_TEXT = """
inputOutputBuffer size: 33280
\tInputs:
\t\tinput_1: offset 0, size: 32768
\tOutput: offset 32768, size: 512

configFilterBuffer size: 208896
\t Config: offset 0, size: 22528
\t Filter: offset 22528, size: 186368
\t Bias+Scale: offset 208896, size: 0

interBuffer size: 0
"""

AD_TOYCAR_TEXT = """
inputOutputBuffer size: 3072
\tInputs:
\t\tinput_1: offset 0, size: 1536
\tOutput: offset 1536, size: 1536

configFilterBuffer size: 564224
\t Config: offset 0, size: 11264
\t Filter: offset 11264, size: 552960
\t Bias+Scale: offset 564224, size: 0

interBuffer size: 0
"""

DS_CNN_TEXT = """
inputOutputBuffer size: 2048
\tInputs:
\t\tinput_1: offset 0, size: 1536
\tOutput: offset 1536, size: 512

configFilterBuffer size: 1573120
\t Config: offset 0, size: 32768
\t Filter: offset 32768, size: 1540352
\t Bias+Scale: offset 1573120, size: 0

interBuffer size: 16960
"""

VWW_TEXT = """
inputOutputBuffer size: 295424
\tInputs:
\t\tinput_1: offset 0, size: 294912
\tOutput: offset 294912, size: 512

configFilterBuffer size: 926720
\t Config: offset 0, size: 72704
\t Filter: offset 72704, size: 854016
\t Bias+Scale: offset 926720, size: 0

interBuffer size: 202752
"""


# ---- parse_ddr_buffer_info(): all four real samples --------------------------------------------

@pytest.mark.parametrize("text,expect", [
    (RESNET8_TEXT, dict(io=33280, cfg_filter=208896, inter=0, cfg=22528, filt=186368,
                        in_size=32768, out_off=32768, out_size=512)),
    (AD_TOYCAR_TEXT, dict(io=3072, cfg_filter=564224, inter=0, cfg=11264, filt=552960,
                          in_size=1536, out_off=1536, out_size=1536)),
    (DS_CNN_TEXT, dict(io=2048, cfg_filter=1573120, inter=16960, cfg=32768, filt=1540352,
                       in_size=1536, out_off=1536, out_size=512)),
    (VWW_TEXT, dict(io=295424, cfg_filter=926720, inter=202752, cfg=72704, filt=854016,
                    in_size=294912, out_off=294912, out_size=512)),
])
def test_parse_real_samples(text, expect):
    layout = parse_ddr_buffer_info(text)
    assert layout.input_output_buffer_size == expect["io"]
    assert layout.config_filter_buffer_size == expect["cfg_filter"]
    assert layout.inter_buffer_size == expect["inter"]
    assert layout.config.offset == 0
    assert layout.config.size == expect["cfg"]
    assert layout.filter.offset == expect["cfg"]
    assert layout.filter.size == expect["filt"]
    assert layout.bias_scale.size == 0
    single = layout.single_input()
    assert single.offset == 0
    assert single.size == expect["in_size"]
    assert layout.output.offset == expect["out_off"]
    assert layout.output.size == expect["out_size"]


def test_parse_contiguity_invariants_hold_for_all_samples():
    """config immediately followed by filter(+bias/scale); output immediately after input --
    the allocator contract cited in device_memory_allocator.cpp, confirmed on real compiler output."""
    for text in (RESNET8_TEXT, AD_TOYCAR_TEXT, DS_CNN_TEXT, VWW_TEXT):
        layout = parse_ddr_buffer_info(text)
        assert layout.filter.offset == layout.config.offset + layout.config.size
        assert layout.bias_scale.offset == layout.filter.offset + layout.filter.size
        assert layout.bias_scale.offset + layout.bias_scale.size == layout.config_filter_buffer_size
        single = layout.single_input()
        assert layout.output.offset == single.offset + single.size
        assert layout.output.offset + layout.output.size == layout.input_output_buffer_size


def test_parse_missing_section_raises():
    with pytest.raises(ValueError, match="config_filter_buffer_size"):
        parse_ddr_buffer_info("inputOutputBuffer size: 10\ninterBuffer size: 0\n")


def test_parse_missing_region_raises():
    broken = "inputOutputBuffer size: 10\nconfigFilterBuffer size: 5\ninterBuffer size: 0\n"
    with pytest.raises(ValueError, match="Output"):
        parse_ddr_buffer_info(broken)


# ---- resolve_hyperram_layout(): guard-banding + placement ---------------------------------------

def test_resolve_places_regions_low_to_high_with_guard_bands():
    layout = parse_ddr_buffer_info(RESNET8_TEXT)
    hy = resolve_hyperram_layout(layout, base_addr=0, align_bytes=512, guard_bytes=512)

    # intermediate is zero-sized for resnet8 -> config sits right after the guard band
    assert hy.intermediate_addr == 0
    assert hy.intermediate_reserved_bytes == 0
    assert hy.config_base_addr == 512          # guard_bytes past intermediate_addr, already aligned
    assert hy.total_config_bytes == 22528      # Config-only, NOT config+filter
    assert hy.config_filter_write_bytes == 208896

    # input sits after config_filter (aligned) + guard band
    expected_input = 512 + ((208896 + 511) // 512) * 512 + 512
    assert hy.input_addr == expected_input
    assert hy.input_bytes == 32768
    # output immediately follows input, same offset the compiler reported -- NOT guard-banded
    assert hy.output_addr == hy.input_addr + 32768
    assert hy.output_bytes == 512
    assert hy.end_addr == hy.input_addr + 33280  # already a multiple of 512


def test_resolve_every_host_write_base_has_guard_dead_space_below_it():
    """The write-wound law (docs/coredla_hyperram_onboard_findings.md §7): every HOST write base
    must have >= guard_bytes of space below it that holds nothing we still need."""
    for text in (RESNET8_TEXT, AD_TOYCAR_TEXT, DS_CNN_TEXT, VWW_TEXT):
        layout = parse_ddr_buffer_info(text)
        hy = resolve_hyperram_layout(layout, guard_bytes=512, align_bytes=512)
        # config_base_addr: below it is (at most) the intermediate reservation, then dead space
        assert hy.config_base_addr - (hy.intermediate_addr + hy.intermediate_reserved_bytes) >= 512
        # input_addr: below it is the config+filter blob, then dead space
        config_filter_end = hy.config_base_addr + hy.config_filter_write_bytes
        assert hy.input_addr - config_filter_end >= 512


def test_resolve_regions_do_not_overlap():
    for text in (RESNET8_TEXT, AD_TOYCAR_TEXT, DS_CNN_TEXT, VWW_TEXT):
        layout = parse_ddr_buffer_info(text)
        hy = resolve_hyperram_layout(layout)
        spans = [
            (hy.intermediate_addr, hy.intermediate_addr + hy.intermediate_reserved_bytes),
            (hy.config_base_addr, hy.config_base_addr + hy.config_filter_write_bytes),
            (hy.input_addr, hy.end_addr),
        ]
        spans.sort()
        for (s0, e0), (s1, e1) in zip(spans, spans[1:]):
            assert e0 <= s1, f"overlap: {spans}"


def test_resolve_rejects_bad_alignment_args():
    layout = parse_ddr_buffer_info(RESNET8_TEXT)
    with pytest.raises(ValueError):
        resolve_hyperram_layout(layout, align_bytes=0)
    with pytest.raises(ValueError):
        resolve_hyperram_layout(layout, guard_bytes=-1)


def test_resolve_defaults_match_vendor_burst_granule():
    assert DEFAULT_ALIGN_BYTES == 512
    assert DEFAULT_GUARD_BYTES == 512


# ---- build_inference_job(): wiring into the CSR handshake ---------------------------------------

def test_build_inference_job_matches_layout():
    layout = parse_ddr_buffer_info(RESNET8_TEXT)
    hy = resolve_hyperram_layout(layout)
    job = build_inference_job(hy)
    assert job.config_base_addr == hy.config_base_addr
    assert job.total_config_bytes == hy.total_config_bytes
    assert job.input_addr == hy.input_addr
    assert job.intermediate_addr == hy.intermediate_addr
    # and the range arithmetic already unit-tested in coredla_csr_handshake keeps working
    assert job.config_range_minus_two() == (hy.total_config_bytes // 8) - 2
