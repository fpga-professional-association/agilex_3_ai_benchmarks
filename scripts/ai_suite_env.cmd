@echo off
call "C:\altera_pro\openvino_2025.4.0\setupvars.bat" || exit /b 1
call "C:\altera_pro\2026.1.1\fpga_ai_suite\dla\setupvars.bat" || exit /b 1
set "QUARTUS_ROOTDIR=C:\altera_pro\26.1\quartus"
