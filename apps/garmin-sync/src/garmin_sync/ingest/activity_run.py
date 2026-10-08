"""Shared policy for one activity archive run across direct and Prefect adapters."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from garmin_sync.ingest.object_registry import ACTIVITIES
from garmin_sync.ingest.results import IngestResult, aggregate_results


ActivityStep = Literal["detail", "file"]


def activity_step_result(
    step: ActivityStep,
    *,
    succeeded: bool,
    error: str | None = None,
) -> IngestResult:
    """Describe an optional activity step, including its result metric."""
    metrics = {
        f"{step}_rows": int(succeeded),
        f"{step}_errors": int(not succeeded),
    }
    if succeeded:
        return IngestResult.success(ACTIVITIES, metrics=metrics)
    return IngestResult(
        data_type=ACTIVITIES,
        status="error",
        errors=1,
        metrics=metrics,
        error=error,
    )


@dataclass(frozen=True)
class ActivityArchiveRun:
    """Choose optional work and assemble one activity's observable outcome."""

    dry_run: bool = False
    include_details: bool = True
    include_files: bool = True

    def complete(
        self,
        summary: IngestResult,
        execute: Callable[[ActivityStep], IngestResult],
    ) -> IngestResult:
        """Run eligible steps in order; leave each adapter in charge of execution."""
        if summary.status != "success":
            return summary

        parts = [summary]
        if self.include_details:
            parts.append(execute("detail"))
        if self.include_files and not self.dry_run:
            parts.append(execute("file"))

        result = aggregate_results(ACTIVITIES, parts)
        errors = list(dict.fromkeys(part.error for part in parts if part.error))
        return replace(result, error="; ".join(errors) if errors else None)
