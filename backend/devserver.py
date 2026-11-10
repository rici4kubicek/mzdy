"""Run the self-hosted server for development with a throwaway data directory.

    uv run python devserver.py   # http://localhost:8000, "admin" / "admin-password"

Serves the built frontend from ../frontend/dist and the API under /api. Data lives
in a temporary directory and is lost on exit. E-mails are sent only when SMTP_HOST
is set. Use --data DIR to keep data between runs.
"""

from __future__ import annotations

import os
import sys
import tempfile

if "--data" in sys.argv:
    i = sys.argv.index("--data")
    data_dir = sys.argv[i + 1]
    del sys.argv[i : i + 2]
else:
    data_dir = tempfile.mkdtemp(prefix="payroll-")

os.environ.setdefault("DATA_DIR", data_dir)
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin-password")

from payroll.server import main  # noqa: E402

if __name__ == "__main__":
    print(f"Data directory: {os.environ['DATA_DIR']}")
    main()
