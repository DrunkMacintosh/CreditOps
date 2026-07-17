from __future__ import annotations

import pytest

from creditops.worker.main import main


def test_unimplemented_worker_fails_instead_of_reporting_success() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main()

    assert exc_info.value.code == 78
