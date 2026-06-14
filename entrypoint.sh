#!/bin/sh
set -e

apply_migrations=0

for arg in "$@"; do
    shift
    if [ "$arg" = "--apply-migrations" ]; then
        apply_migrations=1
    else
        set -- "$@" "$arg"
    fi
done

if [ "$apply_migrations" -eq 1 ]; then
    /app/.venv/bin/python apply-migrations.py
fi

# Execute radegast-console run with the remaining arguments
exec /app/.venv/bin/radegast-console run "$@"
