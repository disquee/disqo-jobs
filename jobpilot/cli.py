"""jobpilot command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from .config import CSV_PATH, load_config
from .models import Application, Status
from .pipeline.discover import discover
from .pipeline.prep import render_prep_file
from .pipeline.process import score_pending, tailor_above_threshold, tailor_job_full
from .store import (
    counts_by_status,
    get_application,
    get_job,
    init_db,
    list_jobs,
    save_application,
    save_job,
)

app = typer.Typer(help="AI job-application assistant.", no_args_is_help=True)
console = Console()


def discover_cmd(
    score: bool = typer.Option(True, help="Score new jobs after discovery."),
    tailor: bool = typer.Option(False, help="Also tailor jobs above the fit threshold."),
):
    """Pull jobs from all configured sources into the local queue."""
    init_db()
    summary = discover()
    console.print(
        f"[green]Discovered[/] {summary['found']} → "
        f"{summary['after_filter']} after filters, "
        f"[bold]{summary['new']}[/] new."
    )
    if summary["unavailable_sources"]:
        console.print(
            f"[yellow]Skipped (no API key):[/] "
            f"{', '.join(summary['unavailable_sources'])}"
        )
    if score:
        n = score_pending()
        console.print(f"[green]Scored[/] {n} job(s).")
    if tailor:
        n = tailor_above_threshold()
        console.print(f"[green]Tailored[/] {n} job(s) above threshold.")


# typer maps function name 'discover_cmd' -> 'discover-cmd'; expose as 'discover'
app.command(name="discover")(discover_cmd)


@app.command()
def status():
    """Show counts by pipeline stage."""
    init_db()
    counts = counts_by_status()
    table = Table(title="jobpilot status")
    table.add_column("Stage")
    table.add_column("Count", justify="right")
    for st in Status:
        table.add_row(st.value, str(counts.get(st.value, 0)))
    console.print(table)
    console.print(f"CSV log: {CSV_PATH}")


@app.command()
def review():
    """List tailored jobs awaiting your approval (use the dashboard to act)."""
    init_db()
    threshold = int(load_config().get("fit_threshold", 70))
    jobs = sorted(
        list_jobs(Status.tailored) + list_jobs(Status.scored),
        key=lambda j: j.fit_score or 0,
        reverse=True,
    )
    table = Table(title=f"Review queue (threshold {threshold})")
    table.add_column("Fit", justify="right")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Status")
    table.add_column("ID")
    for job in jobs[:50]:
        table.add_row(
            str(job.fit_score or "-"), job.title[:40], job.company[:24],
            job.status.value, job.id,
        )
    console.print(table)
    console.print("\nStart the dashboard to review & apply:  jobpilot serve")


@app.command()
def tailor(job_id: str):
    """Generate tailored resume/cover/answers for a single job id."""
    init_db()
    job = get_job(job_id)
    if not job:
        console.print(f"[red]No job with id {job_id}[/]")
        raise typer.Exit(1)
    tailor_job_full(job)
    console.print(f"[green]Tailored[/] {job.title} @ {job.company}")


@app.command()
def apply(job_id: str):
    """Open assisted-apply for a job (autofills, you submit). Logs to CSV after."""
    from datetime import date

    from .apply.autofill import assisted_apply
    from .log_csv import append_application

    init_db()
    job = get_job(job_id)
    if not job:
        console.print(f"[red]No job with id {job_id}[/]")
        raise typer.Exit(1)
    application = get_application(job_id)
    if application is None:
        console.print("[yellow]Not tailored yet — tailoring now…[/]")
        application = tailor_job_full(job)

    assisted_apply(job, application)

    confirm = typer.confirm("Did you submit this application?", default=True)
    if confirm:
        application.date_applied = date.today().isoformat()
        save_application(application)
        append_application(job, application)
        job.status = Status.applied
        save_job(job)
        console.print(f"[green]Logged to[/] {CSV_PATH}")
    else:
        console.print("[yellow]Not logged. You can run apply again later.[/]")


@app.command()
def prep(
    data: str = typer.Argument(..., help="Path to a prep JSON file."),
    out: str = typer.Option(None, "--out", "-o", help="Output .html path."),
    open_after: bool = typer.Option(True, "--open/--no-open", help="Open the page when done."),
    docs: bool = typer.Option(True, "--docs/--no-docs", help="Also write Markdown + PDF."),
):
    """Render an interactive interview-prep page from a prep JSON file."""
    from pathlib import Path

    src = Path(data)
    if not src.exists():
        console.print(f"[red]No such file:[/] {src}")
        raise typer.Exit(1)
    try:
        written = render_prep_file(src, Path(out) if out else None, docs=docs)
    except ValueError as e:
        console.print(f"[red]Invalid prep data:[/] {e}")
        raise typer.Exit(1)
    for kind, path in written.items():
        console.print(f"[green]Wrote[/] {path} ({path.stat().st_size // 1024} KB)")
    dest = written["html"]
    if open_after:
        import subprocess
        import sys

        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(dest)], check=False)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Launch the local review dashboard."""
    import uvicorn

    init_db()
    console.print(f"[green]Dashboard:[/] http://{host}:{port}")
    uvicorn.run("jobpilot.dashboard.server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
