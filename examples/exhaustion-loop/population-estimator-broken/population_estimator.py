from __future__ import annotations

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


def estimate_population(city: str) -> dict[str, object]:
    key = city.strip().lower()
    values = BOUNDARY_VALUES[key]
    estimate = values["municipality"]
    return {
        "city": city,
        "estimate": estimate,
        "units": "people",
    }


if __name__ == "__main__":
    for city in ("Athens", "London"):
        print(estimate_population(city))
