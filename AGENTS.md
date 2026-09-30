# Repository Guidelines

## Project Structure & Module Organization
Core strategy code lives in `volume_farming_strategy.py`, orchestrating the trading loop and state persistence. `strategy_logic.py` holds pure calculations, `aster_api_manager.py` wraps both spot and perp REST calls, and `two_leg.py` is the shared two-leg safety primitive (keep it byte-identical across bots). See `CLAUDE.md` for the safety model. Configuration and state artifacts sit at the repo root (`config_volume_farming_strategy.json`, `volume_farming_state.json`, logs). Offline checks (fake exchange, no network) reside in `tests/`, and Docker assets (`Dockerfile`, `docker-compose.yml`) enable containerized runs.

## Build, Test, and Development Commands
- `python -m venv .venv && . .venv/bin/activate`: create and activate the local virtual environment (Windows PowerShell users can run `.venv\Scripts\Activate.ps1`).
- `pip install -r requirements.txt`: install runtime and toolchain dependencies.
- `python volume_farming_strategy.py`: start the live delta-neutral loop using `aster.env` (credentials; template `aster.env.example`) and the state file.
- `python -m pytest tests -q`: offline checks of the trade path and decision rules; no keys or network needed.
- `docker-compose up --build`: build and launch the bot with the bundled Docker image for reproducible deployments.

## Coding Style & Naming Conventions
Keep compatibility with Python 3.9 (Docker uses 3.11). Follow PEP 8 defaults: four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and module-level constants in `UPPER_CASE`. Prefer explicit imports over wildcards, and keep functions pure inside `strategy_logic.py` so they stay testable. Add docstrings for any public coroutine or helper exposed to other modules.

## Testing Guidelines
The repository uses `pytest`; tests live in `tests/test_*.py` and run offline against a fake exchange (`FakeAster` in `tests/test_offline.py`). Any non-trivial rule gets one check there. Never add tests that place real orders.

## Commit & Pull Request Guidelines
Write concise, imperative commit titles under 72 characters (e.g., `improve`/`fix`/`refactor` prefixes). Include context in the body about risk controls, API surface changes, and required config updates. Pull requests should link relevant issues or tasks, summarize strategic changes, list manual or automated test evidence, and attach screenshots or metrics when UI output changes. Highlight any new environment variables or scheduled jobs.

## Security & Configuration Tips
Never commit `aster.env`, API keys, or generated state files; credentials are read only by `AsterApiManager.from_env()`. Validate any new environment variable names in `README.md` and `docker-compose.yml`. If you script migrations for stored state, provide idempotent upgrade logic so older checkpoints continue to load safely. Rotate keys immediately after test runs that touch production balances.
