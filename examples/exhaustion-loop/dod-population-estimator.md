# Definition of Done: Population Estimator

The target directory contains a deliberately broken estimator.

The fixer must produce a version that satisfies all of the following:

1. `estimate_population(city: str, boundary: str = "metro")` is the public API.
2. The accepted boundaries are exactly:
   - `municipality`
   - `urban`
   - `metro`
3. The return object must include:
   - `city`
   - `boundary`
   - `estimate`
   - `units`
4. The default boundary must be `metro`.
5. Unknown cities must raise `ValueError` with a clear message containing `Unknown city`.
6. Unknown boundaries must raise `ValueError` with a clear message containing `Unknown boundary`.
7. The included tests must pass without editing the tests.
8. Do not add network access, external APIs, or package dependencies.

Reference values to preserve:

- Athens:
  - municipality: `643452`
  - urban: `3041131`
  - metro: `3638281`
- London:
  - municipality: `8799800`
  - urban: `9748000`
  - metro: `14900000`
