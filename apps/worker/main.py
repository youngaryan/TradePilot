from __future__ import annotations

import argparse
import json

from pairs_trading.backend.config import BackendSettings
from pairs_trading.platform import SQLiteMetadataStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect local quant app worker metadata.")
    parser.add_argument("--kind", choices=("paper", "backtest"), help="Optionally list jobs for one job kind.")
    args = parser.parse_args()

    settings = BackendSettings.from_env()
    store = SQLiteMetadataStore(settings.metadata_db_path)
    counts = store.counts()
    payload: dict[str, object] = {
        "metadata_db_path": str(settings.metadata_db_path),
        "counts": {
            "jobs": counts.jobs,
            "deployment_configs": counts.deployment_configs,
            "experiment_runs": counts.experiment_runs,
        },
    }
    if args.kind:
        payload["jobs"] = store.list_jobs(kind=args.kind)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
