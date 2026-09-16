from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_text():
    """Read a captured `format=text` body (or error body) by file name."""

    def read(name: str) -> str:
        return (FIXTURES / name).read_text()

    return read
