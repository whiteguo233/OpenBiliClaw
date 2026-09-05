"""Allow ``python -m openbiliclaw.worker`` to start the background worker."""

from __future__ import annotations

from openbiliclaw.worker.main import main

if __name__ == "__main__":
    main()
