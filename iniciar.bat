@echo off
title Finanzas personales
cd /d "%~dp0"
echo Abriendo Finanzas personales en http://127.0.0.1:8765
echo Cierra esta ventana para detener la aplicacion.
".venv\Scripts\python.exe" -m finanzas iniciar
if errorlevel 1 pause
