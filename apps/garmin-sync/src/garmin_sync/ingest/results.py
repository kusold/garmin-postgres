from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


IngestStatus = Literal["success", "partial", "error"]


@dataclass(frozen=True)
class IngestResult:
    """Serializable result for one ingest object."""

    data_type: str
    status: IngestStatus
    rows: int = 0
    errors: int = 0
    metrics: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(
        cls,
        data_type: str,
        *,
        rows: int = 0,
        metrics: dict[str, int] | None = None,
    ) -> IngestResult:
        return cls(data_type=data_type, status="success", rows=rows, metrics=metrics or {})

    @classmethod
    def from_counts(
        cls,
        data_type: str,
        *,
        rows: int,
        errors: int,
        metrics: dict[str, int] | None = None,
    ) -> IngestResult:
        status: IngestStatus = "success"
        if errors:
            status = "partial" if rows else "error"
        return cls(
            data_type=data_type,
            status=status,
            rows=rows,
            errors=errors,
            metrics=metrics or {},
        )

    @classmethod
    def error_result(
        cls,
        data_type: str,
        *,
        error: str,
        rows: int = 0,
        errors: int = 1,
        metrics: dict[str, int] | None = None,
    ) -> IngestResult:
        return cls(
            data_type=data_type,
            status="error" if rows == 0 else "partial",
            rows=rows,
            errors=errors,
            metrics=metrics or {},
            error=error,
        )

    def as_dict(self) -> dict:
        result = {
            "status": self.status,
            "rows": self.rows,
            "errors": self.errors,
            **self.metrics,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class IngestSummary:
    """Collection of ingest results for one user."""

    results: tuple[IngestResult, ...]

    def as_dict(self) -> dict[str, dict]:
        return {result.data_type: result.as_dict() for result in self.results}

    @property
    def has_errors(self) -> bool:
        return any(result.status == "error" for result in self.results)

    @property
    def has_partials(self) -> bool:
        return any(result.status == "partial" for result in self.results)


def aggregate_results(data_type: str, results: list[IngestResult]) -> IngestResult:
    rows = sum(result.rows for result in results)
    errors = sum(result.errors for result in results)
    metric_names = {
        metric_name
        for result in results
        for metric_name in result.metrics
    }
    metrics = {
        metric_name: sum(result.metrics.get(metric_name, 0) for result in results)
        for metric_name in sorted(metric_names)
    }

    if any(result.status == "error" for result in results):
        status: IngestStatus = "partial" if rows else "error"
    elif any(result.status == "partial" for result in results):
        status = "partial"
    else:
        status = "success"

    return IngestResult(
        data_type=data_type,
        status=status,
        rows=rows,
        errors=errors,
        metrics=metrics,
    )
