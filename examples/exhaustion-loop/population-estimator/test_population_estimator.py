from population_estimator import estimate_population


def test_athens_uses_explicit_boundary() -> None:
    result = estimate_population("Athens")
    assert result["city"] == "Athens"
    assert result["boundary"] == "metro"
    assert result["estimate"] == 3_638_281


def test_london_uses_explicit_boundary() -> None:
    result = estimate_population("London")
    assert result["city"] == "London"
    assert result["boundary"] == "metro"
    assert result["estimate"] == 14_900_000


def test_unknown_city_raises_clear_error() -> None:
    try:
        estimate_population("Paris")
    except ValueError as exc:
        assert "Unknown city" in str(exc)
    else:
        raise AssertionError("Expected ValueError for an unknown city")
