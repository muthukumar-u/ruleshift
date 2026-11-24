"""Optional offline validation against Datadog's official generated API models.

Install datadog-api-client in an isolated environment, then run:
  python tests/validate_datadog_models.py reports/RUN_ID/datadog

No account, credentials, API call or Datadog runtime dependency is required.
"""
import json
from pathlib import Path
import sys

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v1.model.dashboard import Dashboard
from datadog_api_client.v2.model.http_log import HTTPLog
from datadog_api_client.v2.model.metric_payload import MetricPayload


def main():
    root = Path(__file__).resolve().parents[1]
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    client = ApiClient(Configuration())
    board = client.deserialize((root / "monitoring/datadog/dashboard.json").read_text(), (Dashboard,), True)
    print(f"Official Datadog schema: {len(board.widgets)} dashboard widgets validated")
    if report:
        client.deserialize((report / "metrics.json").read_text(), (MetricPayload,), True)
        client.deserialize((report / "logs.json").read_text(), (HTTPLog,), True)
        count = len(json.loads((report / "logs.json").read_text()))
        print(f"Official Datadog schemas: metric series and {count} log events validated")


if __name__ == "__main__":
    main()
