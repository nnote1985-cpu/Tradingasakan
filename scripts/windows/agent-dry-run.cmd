@echo off
rem Double-click to run 3 agent-v1 decisions dry (real models, live Binance data, no book, no orders).
rem Same as: uv run python scripts/agent_dry_run.py --decisions 3
rem Needs AGENT_ANTHROPIC_KEY (setx AGENT_ANTHROPIC_KEY "...", then open a new window). Caps: $1/day, $25/month.
cd /d "%~dp0..\.."
uv run python scripts/agent_dry_run.py --decisions 3
echo.
echo Results are in var\agent_dry_run\. Tell Claude in the project thread when it is done.
pause
