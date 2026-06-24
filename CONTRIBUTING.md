# Contributing

Contributions are welcome when they keep the project runnable without private
infrastructure.

## Development Checks

Run these before opening a pull request:

```bash
python -m py_compile *.py
for script in run_*.sh; do bash -n "$script"; done
```

If your change touches storage behavior, test at least `STORAGE_BACKEND=csv`.
Use `STORAGE_BACKEND=postgres` or `both` only with your own PostgreSQL instance.

## Data and Secrets

Do not commit:

- `.env`
- provider tokens
- database credentials
- logs
- CSV output under `data/`
- database dumps
- exported F10 text corpora

Use `.env.example` for documenting configuration keys.

## Scope

Keep collectors, storage code, and operational wrappers decoupled from local
machine paths. New jobs should support the same storage modes as the rest of
the project whenever practical: `csv`, `postgres`, and `both`.
