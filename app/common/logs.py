"""One-line JSON logs, easy to filter in CloudWatch Logs Insights."""

import json
import logging

logger = logging.getLogger("rag")
logger.setLevel(logging.INFO)


def log_json(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}, default=str))
