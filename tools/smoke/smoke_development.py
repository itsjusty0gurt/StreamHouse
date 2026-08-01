from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS = (
    ("Streamhouse Hub", "products.hub.hub_main"),
    ("Streamhouse AI", "products.ai.ai_main"),
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="streamhouse-smoke-") as directory:
        environment = os.environ.copy()
        environment.update(
            {
                "QT_QPA_PLATFORM": "offscreen",
                "STREAMHOUSE_DATA_DIR": directory,
                "STREAMHOUSE_SMOKE_TEST": "1",
            }
        )
        for product, module in APPLICATIONS:
            result = subprocess.run(
                [sys.executable, "-m", module],
                cwd=REPOSITORY_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode:
                details = (result.stderr or result.stdout).strip()
                raise RuntimeError(
                    f"{product} development smoke failed with "
                    f"exit code {result.returncode}: {details}"
                )
            print(f"{product} development smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
