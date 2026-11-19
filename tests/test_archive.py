"""Which landing files may be archived. The gate in front of the only command that deletes."""

import pytest

from partner_factory import archive
from partner_factory.archive import LandingFile, Load

FILE = LandingFile("sales_20261001.csv", content_key="abc")
COMPLETE = Load(content_key="abc", records=10, status="Loaded", parsed=10, errors=0)


@pytest.mark.parametrize(
    ("load", "expected"),
    [
        (COMPLETE, archive.LOADED),
        # The copy history only lasts 14 days; the records in RAW still prove the load.
        (Load("abc", 10, status=None, parsed=None, errors=None), archive.LOADED),
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
