# Pierre Fork Notes — `pih-dev/alfa-lb`

Source-of-truth index for **every divergence** between this fork and Moussa's
upstream `moussa11/alfa-lb`. Read this first before pulling code from the fork
into upstream — it tells you what's PR-ready, what's Pierre-specific, and why
each change exists.

> **For Moussa**: every entry in the *Active Divergences* table below is a
> self-contained branch with clean commits. Cherry-pick from the listed branch,
> or open a PR back here against `main` and we'll send it your way ourselves.
> Anything marked **PR-candidate** has been shaped specifically with upstream
> adoption in mind (no Pierre-only assumptions, conservative defaults, fits
> the existing code style).

---

## Repo layout

```
upstream  → https://github.com/moussa11/alfa-lb   (Moussa's canonical repo)
origin    → https://github.com/pih-dev/alfa-lb    (Pierre's fork — this repo)
```

Local clone: `C:/projects/HomeLab/external/alfa-lb/`

### Branch model

| Branch | Purpose |
|---|---|
| `main` | Tracks `upstream/main` 1:1 when there are no diverging features in flight. Merges from upstream land here. |
| `pierre/<feature-name>` | Each in-development feature gets its own branch off `main`. Stays alive until the feature lands upstream OR is explicitly retired as Pierre-only. |
| `pierre-main` *(optional, only if needed)* | Created on demand: a roll-up of all currently-deployed pierre branches when Pierre's HA needs more than one un-merged divergence at once. Not used by default — single-feature work runs straight off the feature branch. |

### Versioning

`manifest.json` `version` on a pierre branch follows the upstream version it's
based on plus a `+pierre.<N>` suffix:

```
upstream:           0.3.1
pierre/foo branch:  0.3.1+pierre.1
                    0.3.1+pierre.2     (after a fix on the same branch)
when upstream → 0.4.0 and we rebase:
                    0.4.0+pierre.1
```

This keeps HACS update-detection honest without polluting upstream's semver.

> **Corrected 2026-08-02.** `pierre/portal-rebuild` had drifted to
> `0.5.0+pierre.session` — a version number invented ahead of upstream, which
> made the fork look like the newer project rather than a downstream of it.
> Renumbered to `0.4.0+pierre.1`, i.e. downstream of Moussa's current `0.4.0`.
> **Never invent a base version upstream has not released.**

### HACS install on Pierre's HA

HACS custom repository points at `https://github.com/pih-dev/alfa-lb`, branch
`main` (or whichever pierre branch is currently deployed). When upstream
releases a new version and we want it, we merge upstream into `main` and HACS
sees the update.

---

## Active divergences

| Branch | Status | Upstream-PR-candidate? | Summary |
|---|---|---|---|
| `pierre/portal-rebuild` | deployed (0.4.0+pierre.1) | no — Pierre-only topology | Integration is now a pure file reader: reads the `alfa-session` HA add-on's `/share/alfa_lb/latest.json` (a separate Playwright container that logs into the web portal and passes Alfa's F5/Shape anti-bot, something aiohttp/pycryptodome can't do). Integration itself does no network I/O — no aiohttp, no pycryptodome, no OTP/reauth flow (the add-on owns auth); coordinator maps stale-file/auth errors from the add-on to `UpdateFailed`. Supersedes the earlier aiohttp portal-rebuild transport in this same branch. |

When a divergence is added, the row should look like:

| `pierre/renew-bundles` | in-dev | yes | Adds `select` + `button` for in-app bundle renewal. Discovers `GetAvailableBundles` + `PurchaseBundle` from AlfaNet app v5.2.86. Includes 2-step arm/fire confirmation gate. |

---

## Retired divergences

*(none yet)*

When a feature merges upstream (or is dropped as Pierre-only-permanent), move
its row here with a `Resolution:` note (`merged upstream in vX.Y.Z` /
`Pierre-only — see WHY-PIERRE-ONLY.md` / `dropped`).

---

## Conventions for adding a divergence

1. Branch from current `main`: `git checkout -b pierre/<feature-name>`
2. One feature per branch. Multiple commits ok, but each should be a clean
   logical step Moussa could read top-to-bottom.
3. Bump `manifest.json` `version` to `<base>+pierre.<N>` on the feature branch.
4. Update *Active divergences* table above with the new row.
5. If it's an upstream-PR-candidate: structure the diff as if you were already
   sending it. Avoid Pierre-only paths/sensor names. Match existing code style
   (`from __future__ import annotations`, snake_case, no `print`, `_LOGGER`
   for diagnostics).
6. If it's Pierre-only-by-design (e.g. depends on Pierre's HA topology),
   write a one-line `WHY-PIERRE-ONLY.md` at the branch root explaining why
   it shouldn't go upstream.

## Conventions for upgrading from upstream

1. `git fetch upstream && git checkout main && git merge --ff-only upstream/main`
2. `git push origin main`
3. For each active `pierre/<feature>` branch: `git rebase main`. If conflicts,
   resolve, bump `+pierre.<N>` counter, force-push the feature branch.
4. Re-deploy to HA from the appropriate branch (or merge to `main` if no
   pierre features are active).
