import csv

from jobpilot import log_csv


def test_writes_header_and_required_columns(tmp_csv, sample_job, sample_application):
    sample_job.fit_score = 84
    date = log_csv.append_application(sample_job, sample_application)

    rows = list(csv.DictReader(tmp_csv.open()))
    assert len(rows) == 1
    row = rows[0]

    # The columns the user explicitly asked for must all be present and correct.
    assert row["date_applied"] == date
    assert row["job_title"] == "Senior Backend Engineer"
    assert row["company"] == "Acme"
    assert row["location"] == "Remote"
    assert "Python" in row["posting_text"]
    assert row["apply_url"] == "https://example.com/apply"
    assert row["fit_score"] == "84"
    assert row["status"] == "applied"


def test_appends_without_duplicating_header(tmp_csv, sample_job, sample_application):
    log_csv.append_application(sample_job, sample_application)
    log_csv.append_application(sample_job, sample_application)

    lines = tmp_csv.read_text().strip().splitlines()
    assert lines[0].startswith("date_applied,")          # one header
    assert sum(1 for line in lines if line.startswith("date_applied,")) == 1
    assert len(lines) == 3                                # header + 2 rows


def test_fieldnames_match_schema(tmp_csv, sample_job, sample_application):
    log_csv.append_application(sample_job, sample_application)
    header = tmp_csv.read_text().splitlines()[0].split(",")
    assert header == log_csv.FIELDS
