# Releasing CATALYX

CATALYX uses **SemVer** (`MAJOR.MINOR.PATCH`). There is **one** version number, kept in sync
across three places:

| Where | What |
|---|---|
| `pyproject.toml` → `version` | the canonical source of truth |
| git tag `vX.Y.Z` (annotated, on `main`) | the immutable release marker |
| `CLAUDE.md` → Recent Changes / `CHANGELOG.md` | the human-readable "what changed" |

> **Pre-1.0.** CATALYX is early and still moving fast — the schemas, skills, and lake tables may
> still change without notice, and the leading `0.` says exactly that. The first tagged release is
> **v0.3.1**. The `vN.M` labels that already exist in `CLAUDE.md`/`CHANGELOG.md` (v2.0 … v3.0) are
> an **informal pre-tag change counter**, not SemVer — `0.3.x` loosely continues that phase
> numbering but honestly inside `0.x`. We move to `1.0.0` only when the data model + skill contract
> are stable enough to promise compatibility.

## When to bump which number (while in 0.x)

The leading `0.` means "no stability promise yet", so the usual MAJOR slot is frozen at `0`:

- **MINOR** (`0.Y`) — a **breaking** change to a contract: a schema in `schemas/` whose shape
  changes incompatibly, a removed/renamed skill or its arguments, a removed lake table or CLI
  command, a data-model pivot (e.g. Thesis→Movement). Anything that invalidates existing `data/`
  documents or a skill call. *(After 1.0 this becomes a MAJOR bump.)*
- **PATCH** (`0.Y.Z`) — everything backward-compatible: a new scorer/module/skill, a new lake
  table or query, a new optional field, a fix, a doc update, or an internal refactor.

When in doubt, ask: *would an existing `data/*.json` file or a documented skill call break?* If yes
→ bump MINOR; if no → bump PATCH.

## Cutting a release

Work happens on `release/<name>` (or a feature branch). To release:

1. **Finish + green.** `uv run pytest -q` passes; `uv run python scripts/build_site.py` builds.
2. **Bump** `pyproject.toml` `version` to `X.Y.Z`.
3. **Changelog.** Make sure the change is recorded:
   - `CLAUDE.md` → *Recent Changes* carries the entry, labelled `vX.Y.Z` (last 5 entries only).
   - When *Recent Changes* exceeds 5 rows, move the oldest verbatim into `CHANGELOG.md`.
   - Add a `## vX.Y.Z — YYYY-MM-DD — <title>` section at the top of `CHANGELOG.md` for the release.
4. **Commit** the version bump on the branch: `chore(release): vX.Y.Z`.
5. **Merge to `main`** with a merge commit (keeps release boundaries visible):
   ```
   git checkout main
   git merge --no-ff release/<name> -m "Release vX.Y.Z: <title>"
   ```
6. **Tag `main`** (annotated):
   ```
   git tag -a vX.Y.Z -m "vX.Y.Z — <title>"
   ```
7. **Push** branch + tag:
   ```
   git push origin main --follow-tags
   ```

`main` is the line of releases; every tag points at a merge commit. Release-prep branches
(`release/*`) can be deleted after the merge.

## Inspecting

```
git tag -l                 # all releases
git show vX.Y.Z            # what a release contains
git log --oneline --first-parent main   # the release line (merge commits only)
```
