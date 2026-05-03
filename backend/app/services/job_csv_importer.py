"""CSV job importer.

Parses a CSV byte buffer into a list of `JobCsvRow` records that can be fed
into the existing `parse_job_text` + matching pipeline.

CSV contract:
  * UTF-8 (BOM tolerated).
  * Header row is required.
  * `description` column is the only hard requirement — that's the JD body
    used by the parser. All other columns are optional and used to enrich
    the JD with labelled prefix lines so the rule-based parser can pick up
    metadata cleanly.

MVP limits:
  * Hard cap of 100 rows per upload (configurable via `MAX_ROWS`).
  * Rows missing the `description` column / empty description are skipped
    with an error rather than crashing the whole import.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Iterable

MAX_ROWS = 100

# All columns we know how to use. Any extra columns are ignored.
ALLOWED_COLUMNS = {
    "job_title", "company", "location", "url",
    "description", "salary", "employment_type",
}
# At minimum we need a description (the JD body).
REQUIRED_COLUMNS = {"description"}


@dataclass
class JobCsvRow:
    """One CSV row, normalised. `error` is non-empty when the row is unusable."""
    row_index: int
    job_title: str = ""
    company: str = ""
    location: str = ""
    url: str = ""
    salary: str = ""
    employment_type: str = ""
    description: str = ""
    error: str = ""

    @property
    def is_usable(self) -> bool:
        return not self.error and bool(self.description.strip())

    def to_jd_text(self) -> str:
        """Compose a labelled JD blob the rule-based parser understands."""
        prefix: list[str] = []
        if self.job_title:
            prefix.append(f"Job Title: {self.job_title}")
        if self.company:
            prefix.append(f"Company: {self.company}")
        if self.location:
            prefix.append(f"Location: {self.location}")
        if self.salary:
            prefix.append(f"Salary: {self.salary}")
        if self.employment_type:
            prefix.append(f"Employment: {self.employment_type}")
        head = "\n".join(prefix)
        return (head + "\n\n" + self.description).strip() if head else self.description.strip()


@dataclass
class CsvImportResult:
    rows: list[JobCsvRow] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    truncated: bool = False
    fatal_error: str = ""

    @property
    def usable_rows(self) -> list[JobCsvRow]:
        return [r for r in self.rows if r.is_usable]


def _decode(data: bytes) -> str:
    """Try UTF-8 (with BOM) first, then latin-1 as a last resort."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalise_header(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "_")


def parse_csv_bytes(data: bytes) -> CsvImportResult:
    """Parse a CSV byte buffer into a `CsvImportResult`. Never raises."""
    if not data:
        return CsvImportResult(fatal_error="Uploaded file is empty.")

    text = _decode(data)
    try:
        reader = csv.reader(io.StringIO(text))
        first = next(reader, None)
    except csv.Error as e:
        return CsvImportResult(fatal_error=f"Could not read CSV: {e}")

    if not first:
        return CsvImportResult(fatal_error="CSV has no rows.")

    headers = [_normalise_header(h) for h in first]
    missing = REQUIRED_COLUMNS - set(headers)
    if missing:
        return CsvImportResult(
            headers=headers,
            fatal_error=(
                f"CSV is missing required column(s): {sorted(missing)}. "
                f"Expected at minimum: {sorted(REQUIRED_COLUMNS)}."
            ),
        )

    out: list[JobCsvRow] = []
    truncated = False
    for raw_index, raw_row in enumerate(reader, start=2):  # start=2 → header is line 1
        if len(out) >= MAX_ROWS:
            truncated = True
            break

        # Skip fully empty rows silently.
        if not any((cell or "").strip() for cell in raw_row):
            continue

        cells = list(raw_row) + [""] * max(0, len(headers) - len(raw_row))
        record = {h: (cells[i] if i < len(cells) else "").strip() for i, h in enumerate(headers)}

        row = JobCsvRow(
            row_index=raw_index,
            job_title=record.get("job_title", ""),
            company=record.get("company", ""),
            location=record.get("location", ""),
            url=record.get("url", ""),
            salary=record.get("salary", ""),
            employment_type=record.get("employment_type", ""),
            description=record.get("description", ""),
        )
        if not row.description:
            row.error = "Empty description column."
        out.append(row)

    return CsvImportResult(rows=out, headers=headers, truncated=truncated)


def iter_usable(rows: Iterable[JobCsvRow]) -> Iterable[JobCsvRow]:
    return (r for r in rows if r.is_usable)
