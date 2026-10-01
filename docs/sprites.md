# Sprites: generating object pictures with Midjourney

Object sprites for the `neon_sprites` theme (`src/roomplanner/data/sprites/midjourney/`) are
generated with `tools/sprites.py` through the Midjourney Discord bot. Why it works this way:
ADR 0012. Command usage: the docstring of `tools/sprites.py`.

## Workflow

Keys: `set -a; . ../SINner/.env; set +a` (the SINner project's `MJ_*` keys) before
`generate`. `sheet` and `cut` work offline.

1. **Prompt.** Add or edit the kind's entry under `subjects:` in `tools/sprite_prompts.yaml`
   (wealth variants: under `variants: high/squatter`). Follow the wording rules below.
2. **Generate.** `uv run python tools/sprites.py generate <kind ...> --attempt N`, run in
   the background. It sends six jobs at a time; the author allows no more than six
   running at once. Each new try of a kind gets a new `--attempt` (the seed comes from
   kind + attempt; grids land in `.sprites_raw/<name>.<attempt>.png`). Finished grids are
   skipped, so rerunning the same command resumes.
3. **Review.** `sheet <kind ...> --attempt N --out <scratchpad>/sheet.png`, then look at
   the sheet. Pick per kind the quadrant that is straight top-down, has the object's back at
   the top, is cut out cleanly, and shows only the object. Done when every kind has a pick
   or is recorded as unusable (retry with a sharper subject and the next attempt).
4. **Pick and cut.** Record `<name>: {quadrant: Q, attempt: N}` in
   `tools/sprite_picks.yaml` (`rotate: 180` etc. when the back is not at the top), then
   `cut <name ...>`.
5. **Check.** Render a building that uses the kind with `--theme neon_sprites -f png` and
   look at it. The example set is in `beispiele/sprites/` (seed 11, several wealth tiers).
6. **Commit** only the sprite files: `data/sprites/`, `tools/`, sprite docs. Other agents
   often work in the same checkout at the same time.

Open work = kinds in `sprite_prompts.yaml` without an entry in `sprite_picks.yaml`, plus
picks with `attempt` below 10 (those are from the older model).

## Conventions

- A sprite faces S: the object's back (the wall side) at the top, front at the bottom,
  width = `along`, height = `deep` from `objects.yaml`. The renderer rotates it by
  `facing`. Prompts say what sits at the top edge ("pillow at the top edge", "monitor along
  the top edge"; cars: front bumper at the top, since they park nose to the wall).
- Wealth variants are `<kind>.high` (used for high and luxury) and `<kind>.squatter`.
  Lookup falls back towards middle, then to the theme's `sprite_fallbacks` stand-in.
- A new object kind gets a prompt and, until its sprite is picked, a stand-in in
  `sprite_fallbacks` of `data/themes/neon_sprites.yaml` (e.g. `office_desk: desk`).
- Stairs, elevator cars, wall screens and whiteboards stay procedural.

## Prompting

- Model: `--v 8.2 --raw` is in every suffix; the account's default model is older, and V8
  takes `--raw`, not `--style raw`. V8.2 follows "top-down" far better than older models.
- One look for the whole set: every prompt carries `--sref` to one of our own grids
  (`.sprites_raw/bed.1.png`). Weights: base 60; `high` 35 on a magenta background (white
  marble survives the keying); `squatter` 40. `generate` re-signs the Discord link, which
  expires after a day.
- Wording that works: "<object> seen from straight above, only the flat top visible",
  plus what sits at the top edge. Neighbouring objects creep in (chairs at desks, beds at
  nightstands): name them in `exclude:` and write "no chair" in the subject.
- Known misses: V8.2 turns `rug` and `table` into tech consoles (older picks kept);
  `iv_stand` only comes as a side view; "shower" returned nothing until rephrased.

## Midjourney limits

When the fast hours run out, jobs vanish without an answer: `generate` prints
`STOP: empty batch` and exits. Single `MISSING` lines are usually a filtered prompt;
rephrase it. The quota resets every few days; rerun the same command then.

## Local sets (ComfyUI)

The `modern` set (`data/sprites/modern/`, theme `modern_sprites`) is rendered with the local
ComfyUI through `tools/sprites_local.py` (img2img from a drawn top-down guide; recipe in its
prompt file `tools/sprite_prompts_modern.yaml`). To review and pick, run the studio:

    uv run python tools/sprite_studio.py [--set modern] [--prompts FILE]

It takes the prompt file as input (whoever wrote it), queues every object without a render,
and shows four variants per attempt in the browser: click one to pick and cut it, "Retry"
queues a new attempt. The prompt file is re-read before each job, so edit a description and
retry. Jobs run one at a time, also across several studios (a lock file); the GPU is shared.

Wealth tiers: only middle (`<kind>`), `high` and `luxury` are designed, each from its own guide.
`low` and `squatter` come from the prompt file's `derived:` section: the picked middle sprite
worn down by img2img (denoise 0.55 / 0.7), so they stay the same object. They wait for that
pick and are queued by it. Sets: `modern` (`--prompts tools/sprite_prompts_modern_wealth.yaml`
for the tiers) and `cyberpunk` (`--set cyberpunk`).

Floor textures: a prompt file's `floors:` section lists `floor.<material>` entries for the
materials of `floor_theme` (see `tools/sprite_prompts_neon2.yaml`). They show up in the studio
after the objects. The guide is the material as the map draws it (fill colour and joints, with
grain), changed by the entry's `guide:` if given. They render with Flux (`floor_model: dev`):
Pony without the object LoRA fills an empty floor with people, with it the floor comes back
flat. The pick is cut into a seamless square (blended with itself shifted by half) of
`texture_cells` × 96 px; give a material an even number of joints per texture (`texture_cells`
a multiple of 2 × `tile`) so the blend keeps them in place. The studio shows a picked floor
repeated 2×2.

"Recreate text description" asks OpenAI (`--openai-model`, default gpt-5-mini) for a new
description and writes it into the prompt file; press Retry afterwards. The key comes from
`OPENAI_API_KEY` or a line `OPENAI_API_KEY=...` in `.env` (gitignored).
