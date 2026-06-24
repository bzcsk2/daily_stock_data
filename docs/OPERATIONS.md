# Operations

## Recommended Setup

Start with CSV storage:

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
./run_daily_sync_batches.sh
```

Move to PostgreSQL when you need concurrent readers, larger datasets, or SQL
queries across collectors.

## Cron

Use `cron.example` as a template. Install only the jobs you need. Heavy jobs
such as tick trades and F10 export can produce large output and should be
scheduled deliberately.

## Logs and Data

Runtime logs are written under `logs/`. CSV outputs and F10 text exports are
written under `DATA_DIR`, defaulting to `./data`.

Both locations are ignored by Git.

## Maintenance Checks

```bash
python -m py_compile *.py
for script in run_*.sh; do bash -n "$script"; done
```
