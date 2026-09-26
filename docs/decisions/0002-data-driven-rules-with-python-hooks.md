# 0002: Data-driven building rules with Python hooks

Status: accepted (2026-09-26)

## Context

Every building type needs its own rules, but many rooms (toilet, storage, stairwell,
corridor) are shared. Pure code makes adding types expensive. Pure data eventually turns
the file format into a programming language.

## Decision

- YAML files, validated with Pydantic: a shared room catalog and one program per
  building type.
- `when:` conditions stay a tiny expression language.
- A program chooses a layout strategy by name and may register Python hooks for
  anything data cannot express.

## Consequences

- A new building type is mostly a YAML file.
- YAML over TOML because programs are deeply nested lists of tables.
- The Pydantic models are the schema for the data files and give clear load errors.

## Addendum (2026-09-26)

The "hooks" are realised as pipeline strategies: a program selects any stage by registered
name or by import path (`layout: my_pkg.layouts:MyLayout`), and a custom strategy may
subclass a built-in one (see the hall layout, ADR 0007).
