@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.main %*
) else (
  py -m app.main %*
)

if errorlevel 1 (
  echo.
  echo A execucao falhou. Consulte logs\radar.log.
  exit /b 1
)

echo.
echo Execucao concluida. Consulte reports\ultimo_relatorio.md.
endlocal
