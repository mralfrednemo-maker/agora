from __future__ import annotations

import pytest
from fastapi import HTTPException

from agora.gateway import validate_max_total_rounds


def test_validate_max_total_rounds_rejects_critic_terminate_above_cap() -> None:
    with pytest.raises(HTTPException) as exc:
        validate_max_total_rounds("critic-terminate", 16)
    assert exc.value.status_code == 400
    assert "max_total_rounds must be <= 15" in str(exc.value.detail)


def test_validate_max_total_rounds_accepts_critic_terminate_at_cap() -> None:
    validate_max_total_rounds("critic-terminate", 15)
