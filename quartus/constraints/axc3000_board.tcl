# Arrow AXC3000 board pinout (issue #7). Source this file from a project's .qsf with
#   source ../constraints/axc3000_board.tcl
# after `set_global_assignment -name DEVICE A3CY100BM16AE7S` / `-name FAMILY "Agilex 3"`.
#
# Only assigns the pins actually needed by a design targeting this board (clock in, reset,
# LEDs, HyperRAM). JTAG is on the device's dedicated SDM/JTAG pins via the on-board Altera USB
# Blaster III (UB3) bridge -- it is never a user-assignable I/O and needs no entry here (see
# quartus/hl_jtag_axc3000/README.md and docs/board_bringup.md, "JTAG").
#
# Port names below are the canonical board-signal names this file assigns *to*; a consuming
# top-level module must name its ports identically (or wrap them) for `source` to resolve, same
# pattern as quartus/smoke and the vendor's own axc3000_pin_assignment.tcl (see Provenance).
#
# Provenance (two independent sources cross-checked; see docs/board_bringup.md for the one
# discrepancy found between them):
#  (1) Arrow "AXC3000 Evaluation Board: User Guide", Document Version 1.2.1, 2025-06-30 --
#      https://github.com/ArrowElectronics/Agilex-3/blob/main/images/AXC3000/AXC3000%20User%20Guide_V1.2.pdf
#      SS3.1 (clock), SS3.3 (push buttons), SS3.5 (user LEDs), SS3.6 (HyperRAM) -- PRIMARY source used below,
#      it is the more current, officially numbered/dated document.
#  (2) github.com/ArrowElectronics/refdes-agilex3, tag QPDS25.1.1_QPDS_REL_PR,
#      axc3000/first_agilex3_refdes/sources/axc3000_pin_assignment.tcl (MIT-0 licensed) -- used to
#      cross-check pin numbers and as the reference for source-able .tcl structure.
#
# Device on this board: A3CY100BM16AE7S ("Y" = no HPS, per PLAN SS1). Set FAMILY/DEVICE in the
# consuming project's own .qsf, not here (this file is device-agnostic pin/IO-standard data only).

########################################################################
# Clock in -- 25 MHz fixed oscillator (fixed-freq XO), single-ended.
# User Guide SS3.1.1: CLK_25M_C @ FPGA pin A7, 25 MHz, I/O standard "1.2V".
# (refdes-agilex3's axc3000_pin_assignment.tcl asserts the same pin, PIN_A7, but under IO_STANDARD
#  "1.3-V LVCMOS" with a comment "used to fake Quartus" -- i.e. Quartus has no exact standard for
#  this rail so the refdes repo picked the nearest one on the high side; the User Guide's own table
#  says 1.2 V. Using the User Guide's 1.2 V here as primary; if the Fitter rejects 1.2V LVCMOS on
#  this pin/bank, fall back to "1.3-V LVCMOS" per the refdes precedent -- see docs/board_bringup.md.)
set_location_assignment PIN_A7 -to CLK_25M_C
set_instance_assignment -name IO_STANDARD "1.2 V" -to CLK_25M_C

########################################################################
# Reset -- active-low USER button (S2). User Guide SS3.3: "User button ... drives the associated
# signal line to a low logic level [when pressed] ... open [when released]. The user is
# responsible for adding an internal weak pullup resistor."
# (There is a second button, S1 @ AG15, wired to the device's own nCONFIG/RSTn -- that is a
#  configuration-reset pin, not user fabric logic, and is not assigned here.)
set_location_assignment PIN_A12 -to USER_BTN
set_instance_assignment -name IO_STANDARD "1.2 V" -to USER_BTN
set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to USER_BTN

########################################################################
# User LEDs (User Guide SS3.5). All active-low. D2 is the RGB LED, D10 is a single red LED.
set_location_assignment PIN_AH22 -to RLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to RLED
set_location_assignment PIN_AK21 -to GLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to GLED
set_location_assignment PIN_AK20 -to BLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to BLED
set_location_assignment PIN_AG21 -to LED1
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to LED1

########################################################################
# HyperRAM (128 Mb Winbond, 1.2 V, x8 HyperBus DDR) -- User Guide SS3.6 / PLAN SS1, SS4.
# Port names (hb_dq/hb_rwds/hb_cs_n/hb_ck/hb_rst_n) match quartus/ph3_hyperram_char/char_top.sv's
# top-level ports so that build's .qsf can `source` this file directly (issue #13's original
# hyperbus_smoke.qsf placeholder used the same convention; that project has since been superseded
# by ph3_hyperram_char and removed).
#
# DQ[0:7], HRESETn and RWDS agree byte-for-byte between both provenance sources above. CSn and
# CLK do NOT -- flagged loudly per AGENTS.md rather than silently picking one:
#   User Guide SS3.6:            CSn = C7,  CLK = B5
#   refdes-agilex3 (QPDS25.1.1): HR_CSn = D8, HR_CLK = D7
# Used the User Guide (more current, dated/versioned, official) as primary below. This MUST be
# re-checked against the AXC3000 schematic (images/AXC3000/SCH-TEI0131-01-P001.PDF in the Arrow
# repo) before this is trusted on real silicon -- see docs/board_bringup.md, "HyperRAM pin
# discrepancy", which this session could not resolve (the schematic PDF would not rasterize in
# this environment).
set_location_assignment PIN_C3 -to hb_dq[0]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[0]
set_location_assignment PIN_C2 -to hb_dq[1]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[1]
set_location_assignment PIN_B4 -to hb_dq[2]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[2]
set_location_assignment PIN_B6 -to hb_dq[3]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[3]
set_location_assignment PIN_D3 -to hb_dq[4]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[4]
set_location_assignment PIN_A4 -to hb_dq[5]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[5]
set_location_assignment PIN_B3 -to hb_dq[6]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[6]
set_location_assignment PIN_C6 -to hb_dq[7]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[7]

set_location_assignment PIN_A6 -to hb_rwds
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_rwds

set_location_assignment PIN_F7 -to hb_rst_n
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_rst_n

# CSn / CLK -- see discrepancy note above. User Guide values used:
set_location_assignment PIN_C7 -to hb_cs_n
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_cs_n
set_location_assignment PIN_B5 -to hb_ck
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_ck
