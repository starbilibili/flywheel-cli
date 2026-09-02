"""Top-level fw command tree."""

from __future__ import annotations

import sys

import typer

from flywheel import __version__
from flywheel.auth.commands import app as auth_app
from flywheel.errors import FlywheelError
from flywheel.evaluation.commands import app as eval_app
from flywheel.resource.commands import app as resource_app
from flywheel.resource.commands import register as register_resource
from flywheel.snapshot.commands import app as snapshot_app


app = typer.Typer(help="Compose registered resources into reproducible Flywheel tasks.")
app.add_typer(auth_app, name="auth")
app.add_typer(resource_app, name="resource")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(eval_app, name="eval")
app.command("register")(register_resource)


@app.command("version")
def version() -> None:
    """Print the installed fw version."""

    typer.echo(__version__)


def main() -> None:
    """Run fw and translate domain errors into stable CLI failures."""

    try:
        app()
    except FlywheelError as error:
        typer.echo(f"error: {error}", err=True)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        typer.echo("error: interrupted", err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
