#!/usr/bin/env python
import os
import sys

from alembic import command
from alembic.config import Config

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))


def main():
    print("Running database migrations...")
    try:
        # Load Alembic configuration
        cfg = Config("alembic.ini")
        # Upgrade database to the latest migration (head)
        command.upgrade(cfg, "head")
        print("Migrations successfully applied.")
    except Exception as e:
        print(f"Error applying migrations: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
