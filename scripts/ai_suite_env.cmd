@echo off
call "C:\altera_pro\openvino_2025.4.0\setupvars.bat" || exit /b 1
call "C:\altera_pro\2026.1.1\fpga_ai_suite\dla\setupvars.bat" || exit /b 1
set "QUARTUS_ROOTDIR=C:\altera_pro\26.1\quartus"
set "PATH=%QUARTUS_ROOTDIR%\bin64;%QUARTUS_ROOTDIR%\sopc_builder\bin;%PATH%"
if exist "%~dp0..\build\fpga_ai\python_deps" set "PYTHONPATH=%~dp0..\build\fpga_ai\python_deps;%PYTHONPATH%"
