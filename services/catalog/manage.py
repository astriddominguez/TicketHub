#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

# Long-running processes get traces; one-off commands (migrate, shell...) don't,
# so they never wait for (or complain about) a tracing collector.
TRACED_COMMANDS = {"runserver": "catalog", "relay_outbox": "catalog-relay"}


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in TRACED_COMMANDS:
        from config.telemetry import configure_telemetry

        configure_telemetry(TRACED_COMMANDS[command])
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
