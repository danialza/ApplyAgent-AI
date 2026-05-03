"""Sample / unit-test-style checks for the CSV job importer.

    python -m tests.test_job_csv_importer
"""
from __future__ import annotations

from app.services.job_csv_importer import MAX_ROWS, parse_csv_bytes


CSV_OK = (
    "job_title,company,location,url,description,salary,employment_type\n"
    "Senior AI Engineer,Cortex,Berlin,https://x.io/a,"
    '"Build RAG pipelines with Python and FastAPI. Required: Python, NLP.",'
    "EUR 80K-110K,Full-time\n"
    "WordPress Developer,Pixel & Co,Remote,https://x.io/b,"
    '"3+ years of WordPress and WooCommerce. PHP, JavaScript.",'
    "GBP 40K-55K,Full-time\n"
).encode("utf-8")


CSV_MISSING_DESC = b"job_title,company\nFoo,Bar\n"
CSV_NO_HEADER = b""
CSV_EXTRA_AND_PARTIAL = (
    "job_title,description,wholly_unknown\n"
    "A,Body A,whatever\n"
    ",,\n"                 # blank row — skipped silently
    "B,,bad\n"             # empty description → row.error set
).encode("utf-8")


def test_parses_well_formed_csv() -> None:
    r = parse_csv_bytes(CSV_OK)
    assert r.fatal_error == ""
    assert len(r.rows) == 2
    assert r.rows[0].job_title == "Senior AI Engineer"
    assert "Python" in r.rows[0].description
    jd = r.rows[0].to_jd_text()
    assert jd.startswith("Job Title: Senior AI Engineer")
    assert "Salary: EUR 80K-110K" in jd
    assert all(row.is_usable for row in r.rows)


def test_missing_required_column() -> None:
    r = parse_csv_bytes(CSV_MISSING_DESC)
    assert r.fatal_error and "description" in r.fatal_error
    assert r.rows == []


def test_empty_file() -> None:
    r = parse_csv_bytes(CSV_NO_HEADER)
    assert r.fatal_error and "empty" in r.fatal_error.lower()


def test_extra_columns_and_partial_rows() -> None:
    r = parse_csv_bytes(CSV_EXTRA_AND_PARTIAL)
    assert r.fatal_error == ""
    # Blank row dropped; "B" row kept but flagged as unusable.
    assert len(r.rows) == 2
    a, b = r.rows
    assert a.is_usable is True and a.job_title == "A"
    assert b.is_usable is False
    assert "Empty description" in b.error


def test_row_limit_truncation() -> None:
    header = "description\n"
    rows = "\n".join(f"job number {i}" for i in range(MAX_ROWS + 25))
    csv_bytes = (header + rows + "\n").encode("utf-8")
    r = parse_csv_bytes(csv_bytes)
    assert r.fatal_error == ""
    assert len(r.rows) == MAX_ROWS
    assert r.truncated is True


def test_decodes_bom() -> None:
    bom = "﻿description\nhello\n".encode("utf-8")
    r = parse_csv_bytes(bom)
    assert r.fatal_error == ""
    assert r.rows[0].description == "hello"


def _run_all() -> None:
    tests = [
        test_parses_well_formed_csv,
        test_missing_required_column,
        test_empty_file,
        test_extra_columns_and_partial_rows,
        test_row_limit_truncation,
        test_decodes_bom,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
