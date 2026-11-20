"""Which landing files may be archived. The gate in front of the only command that deletes."""

from pathlib import Path

import pytest
from snowflake.connector.errors import ProgrammingError

from partner_factory import archive
from partner_factory.archive import LandingFile, Load
from partner_factory.config import load_partner, load_platform

PLATFORM = load_platform(Path("platform/dev.yml"))
PARTNER = load_partner(Path("partners/mercadolocal_br.yml"))

FILE = LandingFile("sales_20261001.csv", content_key="abc")
COMPLETE = Load(content_key="abc", records=10, status="Loaded", parsed=10, errors=0)


@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (COMPLETE, archive.LOADED),
        # The copy history only lasts 14 days. Without it nothing says how many records the
        # file held: 8 of 10 loaded looks the same as 8 of 8, so neither is archived.
        (Load("abc", 10, status=None, parsed=None, errors=None), archive.LOAD_UNVERIFIED),
        (Load("abc", 8, status=None, parsed=None, errors=None), archive.LOAD_UNVERIFIED),
        (None, archive.NOT_LOADED),
        (Load(None, 0, "Load failed", 10, 10), archive.LOAD_FAILED),
        (Load("abc", 8, "Partially loaded", 10, 2), archive.PARTIALLY_LOADED),
        (Load("abc", 8, "Loaded", 10, 0), archive.PARTIALLY_LOADED),
        # Same name delivered again with other content: landing no longer holds what RAW has.
        (Load("other", 10, "Loaded", 10, 0), archive.CHANGED_AFTER_LOAD),
        # Two contents loaded under one name.
        (Load("", 20, "Loaded", 10, 0), archive.CHANGED_AFTER_LOAD),
    ],
)
def test_only_files_loaded_completely_and_unchanged_can_be_archived(
    load: Load | None, expected: str
) -> None:
    assert archive.state(FILE, load) == expected


def test_commands_only_ever_name_plain_file_paths() -> None:
    assert archive._SAFE_PATH.fullmatch("orders/2026/10/06/tiendaviva_orders_20261006_01.jsonl")
    for hostile in ("x'; DROP DATABASE P; --.csv", "a b.csv", "../x.csv", "a\\b.csv", "x;.csv"):
        assert not archive._SAFE_PATH.fullmatch(hostile)
    # The whole relative path, with dots matched literally: removing one file cannot touch
    # another one, in the same folder or in a subfolder.
    assert archive._exact("a/sales_1.csv") == "a/sales_1[.]csv"


def test_a_file_that_cannot_be_moved_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stages = {"LANDING": {"a.csv": "1", "b.csv": "2"}, "HISTORICAL": {}}

    def query(connection: None, sql: str) -> list[dict]:
        command = sql.split()[0]
        if command == "COPY":
            if "'a.csv'" in sql:
                raise ProgrammingError("access denied\nmore detail")
            stages["HISTORICAL"]["b.csv"] = stages["LANDING"]["b.csv"]
            return []
        stage = "HISTORICAL" if sql.split()[1].endswith("HISTORICAL") else "LANDING"
        wanted = [path for path in stages[stage] if "PATTERN" not in sql or path[0] + "[.]" in sql]
        if command == "REMOVE":
            for path in wanted:
                del stages[stage][path]
            return []
        return [{"name": f"s3://b/p/{stage.lower()}/{p}", "md5": stages[stage][p]} for p in wanted]

    monkeypatch.setattr(archive, "query", query)
    files = [LandingFile("a.csv", "1"), LandingFile("b.csv", "2")]
    outcomes = dict(archive.archive(None, PLATFORM, PARTNER, files))

    assert outcomes["b.csv"] == archive.ARCHIVED
    assert outcomes["a.csv"].startswith("left in landing:")
    assert outcomes["a.csv"].endswith("access denied more detail")
    assert stages == {"LANDING": {"a.csv": "1"}, "HISTORICAL": {"b.csv": "2"}}
