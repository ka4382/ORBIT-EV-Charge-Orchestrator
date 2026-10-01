@echo off
rem Runs orbit.ps1 without changing the machine-wide PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0orbit.ps1" %*
