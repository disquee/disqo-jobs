from jobpilot.pipeline import render


def test_md_to_html_headings_bold_lists():
    html = render._md_to_html("# Name\n\n## Skills\n\n- Python\n- **FastAPI**\n\npara")
    assert "<h1>Name</h1>" in html
    assert "<h2>Skills</h2>" in html
    assert "<li>Python</li>" in html
    assert "<strong>FastAPI</strong>" in html
    assert "<p>para</p>" in html


def test_inline_escapes_html():
    assert "&lt;script&gt;" in render._inline("<script>")


def test_render_writes_nonempty_file(tmp_path):
    # Works whether WeasyPrint is installed (.pdf) or falls back (.html).
    out = render.render_pdf("# Hi\n\n- a\n- b", tmp_path / "doc.pdf")
    assert out.exists()
    assert out.stat().st_size > 0
    assert out.suffix in {".pdf", ".html"}


def test_path_helpers_are_stable(sample_job):
    assert render.resume_path(sample_job).name.endswith(f"{sample_job.id}.pdf")
    assert "acme" in render.resume_path(sample_job).name
