# Repository Guidelines

## Project Structure & Module Organization
Core strategy code lives in `volume_farming_strategy.py`, orchestrating the trading loop and state persistence. `strategy_logic.py` holds pure calculations, while `aster_api_manager.py` wraps both spot and perp REST calls. Shared helpers are kept in `utils.py`. Configuration and state artifacts sit at the repo root (`config_volume_farming_strategy.json`, `volume_farming_state.json`, logs). Integration-style checks reside in `tests/`, and Docker assets (`Dockerfile`, `docker-compose.yml`) enable containerized runs.

## Build, Test, and Development Commands
- `python -m venv .venv && . .venv/bin/activate`: create and activate the local virtual environment (Windows PowerShell users can run `.venv\Scripts\Activate.ps1`).
- `pip install -r requirements.txt`: install runtime and toolchain dependencies.
- `python volume_farming_strategy.py`: start the live delta-neutral loop using the active `.env` and state files.
- `pytest tests`: execute leverage and rebalance checks; ensure your `.env` points to sandbox keys before running them.
- `docker-compose up --build`: build and launch the bot with the bundled Docker image for reproducible deployments.

## Coding Style & Naming Conventions
Use Python 3.10+ syntax where helpful but keep compatibility with 3.8. Follow PEP 8 defaults: four-space indentation, `snake_case` for functions and variables, `PascalCase` for classes, and module-level constants in `UPPER_CASE`. Prefer explicit imports over wildcards, and keep functions pure inside `strategy_logic.py` so they stay testable. Add docstrings for any public coroutine or helper exposed to other modules.

## Testing Guidelines
The repository uses `pytest`; tests live in `tests/test_*.py` and expect live credentials via `.env`. Treat them as integration checks: run against the Aster test environment or throttled production keys, and record responses in logs rather than snapshots. When adding new behavior, provide a mockable helper or dry-run pathway so tests can assert on calculations without hitting the exchange.

## Commit & Pull Request Guidelines
Write concise, imperative commit titles under 72 characters (e.g., `improve`/`fix`/`refactor` prefixes). Include context in the body about risk controls, API surface changes, and required config updates. Pull requests should link relevant issues or tasks, summarize strategic changes, list manual or automated test evidence, and attach screenshots or metrics when UI output changes. Highlight any new environment variables or scheduled jobs.

## Security & Configuration Tips
Never commit `.env`, API keys, or generated state files. Validate any new environment variable names in `README.md` and `docker-compose.yml`. If you script migrations for stored state, provide idempotent upgrade logic so older checkpoints continue to load safely. Rotate keys immediately after test runs that touch production balances.
