"""Structured JSON logs and CloudWatch metrics.

Metrics use the Embedded Metric Format (EMF): we just print a specially shaped
JSON line and CloudWatch turns it into a metric. No API call, no extra IAM,
no extra latency. Logs never include document text or questions.
"""

from __future__ import annotations

import json
import time
from typing import Any


def log(level: str, event: str, **fields: Any) -> None:
    print(json.dumps({"level": level, "event": event, **fields}, default=str))


def emit_metric(name: str, value: float, unit: str = "Count") -> None:
    print(
        json.dumps(
            {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": "DocumentQA",
                            "Dimensions": [[]],
                            "Metrics": [{"Name": name, "Unit": unit}],
                        }
                    ],
                },
                name: value,
            }
        )
    )
