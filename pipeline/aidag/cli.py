"""aidag CLI — every pipeline stage is a subcommand.

Stages are idempotent and resumable; only `simulate` and `probe` call the
Anthropic API (and both support --dry-run, which is free).
"""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)


@app.command("fetch-votes")
def fetch_votes(force: bool = typer.Option(False, help="Re-download even if zips exist")) -> None:
    """Download bulk votering dumps and build data/processed/votes.parquet."""
    from aidag.fetch_votes import run

    run(force=force)


@app.command("fetch-cases")
def fetch_cases(retry_failed: bool = typer.Option(False, help="Retry previously failed dok_ids")) -> None:
    """Fetch dokumentstatus JSON for every dok_id referenced by a vote."""
    from aidag.fetch_cases import run

    run(retry_failed=retry_failed)


@app.command("build-cases")
def build_cases() -> None:
    """Join votes + dokumentstatus into cases.parquet and party_positions.parquet."""
    from aidag.build_cases import run

    run()


@app.command("fetch-corpus")
def fetch_corpus(force: bool = typer.Option(False)) -> None:
    """Download party manifestos (SND Vivill) and Tidöavtalet into data/corpus/."""
    from aidag.fetch_corpus import run

    run(force=force)


@app.command("build-kb")
def build_kb(
    month: str = typer.Option(None, help="Build a single YYYY-MM snapshot"),
    force: bool = typer.Option(False),
) -> None:
    """Build monthly point-in-time country-state snapshots in data/kb/snapshots/."""
    from aidag.build_kb import run

    run(month=month, force=force)


@app.command("select-pilot")
def select_pilot(n: int = typer.Option(100), seed: int = typer.Option(2026)) -> None:
    """Pick a stratified pilot sample of cases (seeded, reproducible)."""
    from aidag.select_pilot import run

    run(n=n, seed=seed)


@app.command()
def simulate(
    run_id: str = typer.Option(..., "--run-id"),
    pilot: bool = typer.Option(False, help="Restrict to the pilot selection"),
    party: str = typer.Option(None, help="Single party code, e.g. S"),
    model: str = typer.Option(None, help="Override the default model"),
    arm: str = typer.Option("anonymous", help="'anonymous' or 'labeled' prompt arm"),
    dry_run: bool = typer.Option(False, help="Render prompts + cost report, no API calls"),
) -> None:
    """Submit simulation batches to the Anthropic Batch API."""
    from aidag.simulate import run

    run(run_id=run_id, pilot=pilot, party=party, model=model, arm=arm, dry_run=dry_run)


@app.command()
def collect(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Poll submitted batches and append finished results to JSONL."""
    from aidag.simulate import collect

    collect(run_id=run_id)


@app.command()
def probe(
    run_id: str = typer.Option(..., "--run-id"),
    pilot: bool = typer.Option(False),
    model: str = typer.Option(None),
    dry_run: bool = typer.Option(False),
) -> None:
    """Run the memorization probe (contamination measurement)."""
    from aidag.probe import run

    run(run_id=run_id, pilot=pilot, model=model, dry_run=dry_run)


@app.command()
def aggregate(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Compute agreement stats, confusion matrices and probe analysis."""
    from aidag.aggregate import run

    run(run_id=run_id)


@app.command("export-site")
def export_site(run_id: str = typer.Option(None, "--run-id", help="Omit to export cases without decisions")) -> None:
    """Write per-case JSON + indexes + aggregates into site/src/data/."""
    from aidag.export_site import run

    run(run_id=run_id)


@app.command()
def verify(
    stage: str = typer.Argument(..., help="votes|cases|kb|prompts|simulate|site|all"),
    run_id: str = typer.Option(None, "--run-id"),
) -> None:
    """Read-only integrity checks; exits nonzero on failure (CI gate)."""
    from aidag.verify import run

    raise typer.Exit(code=run(stage=stage, run_id=run_id))


if __name__ == "__main__":
    app()
