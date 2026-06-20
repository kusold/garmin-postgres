from __future__ import annotations

import subprocess


def deploy_all() -> None:
    subprocess.run(["prefect", "deploy", "--all"], check=True)
