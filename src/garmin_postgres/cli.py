import typer

app = typer.Typer(name="garmin-postgres", help="Archive Garmin Connect data into PostgreSQL.")


@app.callback()
def main() -> None:
    """Archive Garmin Connect data into PostgreSQL."""
    return


if __name__ == "__main__":
    app()
