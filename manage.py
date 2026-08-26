#!/usr/bin/env python
"""Headless management entrypoint — this project has no HTTP server, no
views, no urls. It exists only to run `manage.py import_acts` (and the
throwaway `spike_fetch_act`) manually or from a scheduled task, writing into
the same Postgres database ams reads from. See README.md."""
import os
import sys


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'acts_importer.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Activate venv and pip install -r requirements.txt."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
