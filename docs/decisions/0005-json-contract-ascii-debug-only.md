# 0005: JSON is the contract, ASCII is debug-only

Status: accepted (2026-09-26)

## Decision

- The generator is a library (`generate(params) -> Building`) with a thin Typer CLI.
- The full model is exported as versioned JSON (`schema_version`). The JSON mapping is
  written explicitly, so internal refactors do not silently change the contract.
- The future image renderer consumes the JSON (or the in-memory model).
- The ASCII renderer is minimal plain ASCII on a doubled grid and will not be polished;
  it will be dropped once images exist.
