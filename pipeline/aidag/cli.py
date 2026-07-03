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


@app.command("build-worldstate")
def build_worldstate(force: bool = typer.Option(False)) -> None:
    """Build per-date worldstate datasets (economy + events, point-in-time)."""
    from aidag.worldstate import run

    run(force=force)


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


@app.command("agent-prepare")
def agent_prepare(
    run_id: str = typer.Option(..., "--run-id"),
    batch_size: int = typer.Option(240),
    probes: bool = typer.Option(True, help="Include memorization probes"),
) -> None:
    """Emit the next subagent batch manifest from pending work (checkpoint-aware)."""
    from aidag.agent_run import prepare

    prepare(run_id=run_id, batch_size=batch_size, include_probes=probes)


@app.command("agent-status")
def agent_status(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Progress report for a subagent-based run."""
    from aidag.agent_run import status

    status(run_id=run_id)


@app.command("translate-prepare")
def translate_prepare(
    run_id: str = typer.Option(..., "--run-id"),
    batch_size: int = typer.Option(240, help="Translation agents per batch"),
) -> None:
    """Emit the next English-translation batch manifest (checkpoint-aware).

    Run AFTER repair-citations so quote translations use the repaired quotes."""
    from aidag.translate import prepare

    prepare(run_id=run_id, batch_size=batch_size)


@app.command("translate-ingest")
def translate_ingest(
    run_id: str = typer.Option(..., "--run-id"),
    input: str = typer.Option(..., "--input", help="Workflow result JSON ({cases, decisions})"),
    model: str = typer.Option("claude-sonnet-4-6", help="Model the agents ran on"),
) -> None:
    """Ingest a translate workflow batch result (validated, idempotent)."""
    from aidag.translate import ingest

    ingest(run_id=run_id, input_path=input, model=model)


@app.command("translate-status")
def translate_status(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Translation progress report."""
    from aidag.translate import status

    status(run_id=run_id)


@app.command("repair-citations")
def repair_citations(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Align paraphrased citation quotes to the true document span (flagged)."""
    from aidag.repair import run as repair

    repair(run_id=run_id)


@app.command("agent-ingest")
def agent_ingest(
    run_id: str = typer.Option(..., "--run-id"),
    input: str = typer.Option(..., "--input", help="Workflow result JSON ({sims, probes})"),
    model: str = typer.Option("claude-sonnet-4-6", help="Model the agents ran on"),
) -> None:
    """Ingest a workflow batch result into the standard results layout."""
    from aidag.ingest_agent_run import run as ingest

    ingest(run_id=run_id, input_path=input, model=model)


@app.command()
def verify(
    stage: str = typer.Argument(..., help="votes|cases|kb|prompts|simulate|translate|site|all"),
    run_id: str = typer.Option(None, "--run-id"),
) -> None:
    """Read-only integrity checks; exits nonzero on failure (CI gate)."""
    from aidag.verify import run

    raise typer.Exit(code=run(stage=stage, run_id=run_id))


if __name__ == "__main__":
    app()
