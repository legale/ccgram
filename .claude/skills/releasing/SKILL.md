---
name: releasing
description: Full release lifecycle — version bump, CHANGELOG, rich release notes, tag, publish. Use when user says "release", "tag and release", "publish version", "cut a release", "new version".
user-invocable: true
allowed-tools:
  - Bash(git *)
  - Bash(gh *)
  - Bash(git cliff *)
  - Bash(make check)
  - Read
argument-hint: "[patch|minor|major|<version>] (e.g., patch, minor, 2.4.0)"
---

# Release

Tag and publish a new version with rich, narrative release notes.

## Step 1: Pre-flight

Run in parallel:

```bash
git branch --show-current          # Must be main
git status --porcelain             # Must be clean
git log origin/main..HEAD --oneline  # Must be empty (all pushed)
```

**If any fail:** report and stop.

Run `make check` — all must pass.

## Step 2: Determine Version

Get the last tag:

```bash
git describe --tags --abbrev=0
```

Parse `$ARGUMENTS`:

- If a semver like `2.4.0` → use it directly
- If `patch` / `minor` / `major` → bump the last tag accordingly
- If empty → analyze commits since last tag to suggest:
  - `feat` commits → minor
  - Only `fix`/`docs`/`refactor` → patch
  - Breaking changes → major
  - Present suggestion, ask user to confirm

The version is `X.Y.Z` (no `v` prefix in display). The git tag is `vX.Y.Z`.

## Step 3: Generate CHANGELOG

```bash
git cliff --tag vX.Y.Z --output CHANGELOG.md
```

## Step 4: Craft Release Notes

Read the FULL diff since the last tag to understand every change:

```bash
git diff <last-tag>..HEAD -- '*.py' '*.yml' '*.toml'
git log <last-tag>..HEAD --format="%H %s"
```

Also read any PR descriptions referenced in commits:

```bash
gh pr list --state merged --search "is:merged" --limit 20 --json number,title,body
```

Now write release notes following this structure and style (study existing releases for tone):

### Style Rules

1. **Lead with a theme headline** — one sentence summarizing what this release is about
   - Feature release: `## Shell Provider — Chat-First Shell Interface via Telegram`
   - Bug fix release: `## Bug Fixes & Reliability`
   - Mixed: `## Wrap Prompt Mode — Preserve Your Shell Prompt`

2. **Explain WHY, not just WHAT** — don't just list commit messages. Explain user-facing impact:
   - Bad: `- Fix idempotent prompt markers`
   - Good: `- **Idempotent prompt markers** — guards prevent duplicate marker injection on repeated setup (e.g., after exec bash or profile reload)`

3. **Group by theme, not commit type** — if 3 fixes all relate to "shell prompt reliability", group them under that heading instead of a flat "### Fixed" list

4. **Include context** — before/after examples, tables, config snippets where they help

5. **End with Install / Upgrade block** — always include `uv tool upgrade ccgram` and `brew upgrade ccgram`

6. **Keep it concise** — a patch release with 2 fixes needs 15-20 lines, not 100. Scale depth with change magnitude.

7. **No "Documentation" section** — skip changelog/readme commits, they're noise in release notes

8. **Link PRs** where available: `([#36](https://github.com/alexei-led/ccgram/pull/36))`

Present the draft release notes to the user for review before proceeding.

## Step 5: Commit and Tag

```bash
git add CHANGELOG.md
git commit -m "docs: update CHANGELOG.md for vX.Y.Z"
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

**CRITICAL:** The CHANGELOG commit message must NOT contain `[skip ci]` — it kills tag-triggered workflows.

## Step 6: Update GitHub Release

Poll until the release exists (CI creates it), then edit with the crafted notes:

```bash
# Poll every 15s, up to 5 minutes
gh release view vX.Y.Z --json tagName 2>/dev/null
```

Once it exists:

```bash
gh release edit vX.Y.Z --notes "<crafted release notes>"
```

Report final status with link to the release.

## Notes

- hatch-vcs generates version from tag: `v2.4.0` → PyPI `2.4.0`
- Release workflow: `.github/workflows/release.yml` (3 jobs: PyPI, Homebrew, GitHub Release)
- CI uses git-cliff `--latest` for baseline notes; this skill replaces them with rich notes
- Re-tag if needed: `git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`
