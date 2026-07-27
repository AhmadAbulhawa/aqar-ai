from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from time import perf_counter


logger = logging.getLogger("aqar_ai.pipeline")


def log_pipeline_event(
    correlation_id: str,
    stage: str,
    event: str,
    **metadata,
) -> None:
    logger.info(
        json.dumps(
            {
                "correlation_id": correlation_id,
                "stage": stage,
                "event": event,
                **metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    )


@contextmanager
def timed_pipeline_stage(correlation_id: str, stage: str, **metadata):
    started = perf_counter()
    log_pipeline_event(correlation_id, stage, "started", **metadata)
    try:
        yield
    except Exception as exc:
        log_pipeline_event(
            correlation_id,
            stage,
            "failed",
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            error_type=type(exc).__name__,
        )
        raise
    else:
        log_pipeline_event(
            correlation_id,
            stage,
            "completed",
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
