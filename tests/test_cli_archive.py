"""The exit code of `factory archive`: what the scheduler reads to tell success from failure."""

from contextlib import nullcontext

import pytest
from typer.testing import CliRunner

from partner_factory import archive, cli
from partner_factory.archive import LandingFile

READY = LandingFile("sales_20261001.csv", "abc")
ALSO_READY = LandingFile("sales_20261002.csv", "def")
ARRIVED_LATER = LandingFile("sales_20261003.csv", "ghi")


def run_archive(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, str]):
    """Run the command against a landing prefix with two loaded files and one that is not."""
    landing = [
        (READY, archive.LOADED),
        (ALSO_READY, archive.LOADED),
        (ARRIVED_LATER, archive.NOT_LOADED),
    ]
    asked = []

    def move(connection, platform, partner, files):
        asked.extend(files)
        for file in files:
            yield file.path, outcomes[file.path]

    monkeypatch.setattr(cli, "connect", lambda platform: nullcontext())
    monkeypatch.setattr(cli, "_dbt", lambda *arguments: 0)
    monkeypatch.setattr(cli.archiving, "landing_files", lambda *arguments: landing)
    monkeypatch.setattr(cli.archiving, "archive", move)
    result = CliRunner().invoke(cli.app, ["archive", "MERCADOLOCAL_BR"])
    assert asked == [READY, ALSO_READY]
    return result


def test_succeeds_when_every_chosen_file_was_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_archive(
        monkeypatch, {READY.path: archive.ARCHIVED, ALSO_READY.path: archive.ARCHIVED}
    )
    # The file that is not loaded yet waits for the next run: reported, and not a failure.
    assert result.exit_code == 0
    assert ARRIVED_LATER.path in result.output


def test_fails_when_a_chosen_file_was_not_archived(monkeypatch: pytest.MonkeyPatch) -> None:
    result = run_archive(
        monkeypatch,
        {
            READY.path: "left in landing: the copy in historical could not be confirmed",
            ALSO_READY.path: archive.ARCHIVED,
        },
    )
    assert result.exit_code == 1
    # The failure of the first file did not stop the second one from being reported.
    assert "could not be confirmed" in result.output
    assert f"{archive.ARCHIVED:<10}  {ALSO_READY.path}" in result.output
    assert "1 of 2 files could not be archived" in result.output
