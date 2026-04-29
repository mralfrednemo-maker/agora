from __future__ import annotations

VALID_BOUNDARIES = {"municipality", "urban", "metro"}

BOUNDARY_VALUES = {
    "athens": {
        "municipality": 643_452,
        "urban": 3_041_131,
        "metro": 3_638_281,
    },
    "london": {
        "municipality": 8_799_800,
        "urban": 9_748_000,
        "metro": 14_900_000,
    },
}


def estimate_population(city: str, boundary: str = "metro") -> dict[str, object]:
    key = city.strip().lower()
    if key not in BOUNDARY_VALUES:
        raise ValueError(f"Unknown city: {city}")
    if boundary not in VALID_BOUNDARIES:
        raise ValueError(f"Unknown boundary: {boundary}")
    return {
        "city": city,
        "boundary": boundary,
        "estimate": BOUNDARY_VALUES[key][boundary],
        "units": "people",
    }


if __name__ == "__main__":
    for city in ("Athens", "London"):
        print(estimate_population(city))
