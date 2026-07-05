"""Tests for sw/host/l0_golden.py (issue #9).

The two numeric cases here (N_BLOCKS=3/N_VECTORS=5 and N_BLOCKS=1/N_VECTORS=7) are cross-checked
against a REAL Verilator simulation of rtl/microbench/l0_tensor_chain/l0_tensor_chain.sv in
sim/l0_tensor_chain/tb_l0_tensor_chain.sv (run `sim/l0_tensor_chain/run.sh`) — this file only checks
the model's internal self-consistency (determinism, the documented cycle-count formula, seed
distinctness), not RTL agreement (pytest has no Verilator dependency).
"""

import l0_golden as g


def test_cycles_formula_matches_fill_cycles_plus_n_vectors():
    for n_blocks, n_vectors, margin in [(1, 7, 4), (3, 5, 4), (8, 20, 4), (8, 1000, 4), (32, 3, 2)]:
        result = g.run(n_blocks, 10, n_vectors, fill_margin=margin)
        assert result["cycles"] == n_blocks + margin + n_vectors
        assert result["done"] == n_vectors


def test_deterministic_repeatable():
    a = g.run(8, 10, 100)
    b = g.run(8, 10, 100)
    assert a == b


def test_checksum_changes_with_seed_role_i_e_blocks_are_not_identical():
    """Every block has a distinct weight-LFSR seed (seed_for_role(b)); if two blocks' seeds
    collided, N_BLOCKS=2 and N_BLOCKS=1-doubled would be indistinguishable in a way that would be a
    real bug. Cheap proxy: seeds for role 0 and role 1 must differ."""
    assert g.seed_for_role(0, 10) != g.seed_for_role(1, 10)
    assert g.seed_for_role(0, 10) != 0
    assert g.seed_for_role(1, 10) != 0


def test_lfsr_next_is_a_bijection_never_gets_stuck_at_zero_from_nonzero():
    state = g.seed_for_role(0, 10)
    taps = g.taps_mask(80)
    seen = set()
    for _ in range(200):
        assert state != 0, "LFSR reached the zero-lock state (would freeze forever)"
        assert state not in seen, "LFSR repeated a state within 200 steps (suspiciously short cycle)"
        seen.add(state)
        state = g.lfsr_next(state, 80, taps)


def test_to_signed8_and_to_signed32_roundtrip():
    assert g.to_signed8(0x00) == 0
    assert g.to_signed8(0x7F) == 127
    assert g.to_signed8(0x80) == -128
    assert g.to_signed8(0xFF) == -1
    assert g.to_signed32(0x7FFFFFFF) == 2**31 - 1
    assert g.to_signed32(0x80000000) == -(2**31)
    assert g.to_signed32(0xFFFFFFFF) == -1


def test_known_regression_values_n3_v5():
    """Matches sim/l0_tensor_chain/tb_l0_tensor_chain.sv's EXP_* constants exactly — if this test's
    expected values ever need to change, the testbench constants must change with them."""
    result = g.run(3, 10, 5)
    assert result["cycles"] == 12
    assert result["done"] == 5
    assert result["checksum"] == 0x00001909


def test_known_regression_values_n1_v7():
    result = g.run(1, 10, 7)
    assert result["cycles"] == 12
    assert result["done"] == 7
    assert result["checksum"] == 0xFFFF47E0
