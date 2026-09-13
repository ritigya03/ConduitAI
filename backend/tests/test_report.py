import polars as pl

from app.report import generate_report


def test_generate_report_writes_html_file(tmp_path, monkeypatch):
    monkeypatch.setattr("app.report.REPORTS_DIR", tmp_path)
    df = pl.DataFrame(
        {
            "email": ["a@example.com", "b@example.com", None],
            "amount": ["100.00", "200.50", "300.00"],
        }
    )

    output_path = generate_report(df, batch_id="test-batch-123")

    assert output_path.exists()
    assert output_path.parent == tmp_path
    assert output_path.suffix == ".html"
    assert output_path.read_text(encoding="utf-8").strip() != ""
