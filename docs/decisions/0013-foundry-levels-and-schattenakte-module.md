# 0013: Foundry export as one levelled scene, imported by the Schattenakte module

Status: accepted (2026-10-03), supersedes the Foundry part of 0011

## Context

The 0011 export (a folder with images, `scenes.json` and a macro to paste) was too much
work for the people the project is meant for. Existing importers don't fill the gap: the
Universal Battlemap Importer fails on Foundry v14 (images upload to a broken path, several
floors can't be combined), and none keeps floors linked. Foundry v14 has native Scene
Levels, which fit a building better than a scene per floor.

## Decision

- Own Foundry module, **Schattenakte - Shadowrun Battlemap Importer** (`foundry-module/`,
  name chosen by the user): a GM button *Import building* in the Scenes sidebar takes one
  `.schattenakte.json`, uploads its images to `Data/schattenakte/<name>/` and creates the
  scene (replacing an earlier import of it after asking).
- One file per building: `{format: "schattenakte", version, name, scene, images}`, images
  embedded as WebP (about 0.6 MB per floor instead of 6 MB PNG). `-f foundry` and the UI's
  Foundry button produce it.
- Foundry v14+ only: one scene, a level per floor (3 m bands), walls and lights per level,
  stairwells and elevators as Change Level regions over their consecutive floors (the
  layout of Foundry's own Scene Levels demo). v12/v13 and the macro export are dropped.
- Released through GitHub (tag `schattenakte-v<version>`); Foundry installs from the
  `releases/latest/download/module.json` manifest URL.
