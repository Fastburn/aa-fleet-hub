# Fleet Hub

**Fleet doctrine and skill readiness plugin for [Alliance Auth](https://allianceauth.readthedocs.io/).**

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Alliance Auth 5.0+](https://img.shields.io/badge/Alliance%20Auth-5.0%2B-informational)
![Python 3](https://img.shields.io/badge/python-3-blue.svg)

Members see which doctrines they qualify for and exactly which skills they're short. FCs copy a pre-built fleet MOTD and push structure templates directly to their fleet via ESI. Officers manage doctrines, fits, and skill tiers entirely from the web UI, no server access required.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Permissions](#permissions)
- [Configuration](#configuration)
- [Initial skill data](#initial-skill-data)
- [Upgrading](#upgrading)
- [License](#license)

## Features

### For members
- **Doctrines page**: collapsible doctrine cards with a card per fit, each with a "Can you fly it?" line and a plain-word readiness badge (Ready, N skills, Training, Not yet) for the selected character. A "How to read this page" key explains the badges. Role badges, fit notes, and a hull ownership indicator (green glow if you own the hull, greyscale if not) are shown on each fit.
- **Doctrine search**: a search box on the Doctrines page finds doctrines by ship name, with Tag, Ship class and Role dropdowns to narrow further (any selection within a dropdown, all dropdowns combined). A matching doctrine still shows all its fits, with the matching ones highlighted. Ship class comes from EVE's market groups, and roles come from the fits' own Role field, so "Logi", "logi" and "LOGI" are one option. Active filters show as removable chips and are kept in the page URL, so a filtered view can be shared or bookmarked.
- **Doctrine links**: the **Link** button on a doctrine copies a link that opens just that doctrine, with a "Show all doctrines" link back. A doctrine that was removed, or that the viewer can't access, shows the same "Doctrine not found" message, so a link never reveals a doctrine to someone who can't see it.
- **Tag categories**: doctrines with a tag are grouped into sections by tag, with untagged doctrines in a final "Untagged" section. Pinned doctrines come first in their own Pinned section. Within a section, doctrines follow their Sort Order, then name. The same grouping appears on My Skills, Fleet Command, Fleet Setup, and the Manage page, which also have a filter bar to show one category at a time when more than one exists (the Doctrines page uses the search's Tag dropdown instead). Tag badges use the colour set on the doctrine, with black or white text, whichever is easier to read on it.
- **Fit panel**: click any ship to open a slide-in panel with two tabs: the EFT fit and a full skill breakdown. The fit tab includes any PI (Planetary Interaction) templates inline, each plan collapsible to its own header so several can coexist in the panel without crowding it out, plus three action buttons: **Copy EFT** (raw fit text), **Copy Buy All** (multiline ship + module list with quantities for pasting into the EVE client's multiple-item buy window), and **Save to ESI** (pushes the fit directly to your in-game fitting window). The skills tab shows pips for each required skill (trained, short, unmet, or in queue) with a one-click skill plan you can import straight into the EVE client. No fit action buttons here, since they don't apply to the skills view.
- **My Skills**: per-doctrine skill grids broken down by fit tier, with collapsible tiers. A "Closest to qualifying" banner highlights which tier you're nearest to completing, and an "Only show skills I'm missing" filter hides what you've already trained.
- **Character switcher**: members with multiple characters registered in memberaudit can switch between them via a "Viewing as" selector in the navigation bar. Readiness, skill pips, and hull ownership all update to reflect the selected character.
- **Dashboard widget**: opt-in readiness summary on the Alliance Auth home page.

### For Fleet Commanders
- **Fleet Command page**: alliance-wide readiness stats per doctrine. Displays which pilots qualify at each tier, counted both as characters and as pilots (one owner with all their alts, counted once at their best tier). Each doctrine's readiness page lists fits collapsed, showing tier counts and the most common missing skills; expand a fit for its character table, sortable by Character / Corporation / Status (click a column header). Alts and characters who can't fly a fit are hidden by default, with a count of what's hidden and one click to show them; the choice is remembered per user. When shown, alt characters are grouped directly beneath their main (indicated with a `↳` arrow and a subtle row tint).
- **MOTDs** (Fleet Setup): MOTDs are their own records, written in an editor with a live preview of how they look in game and a configurable quick-link dropdown for common Mumble channels or other links. One click to copy. Anyone with Fleet Setup access can make MOTDs and edit or delete their own. A doctrine can have several MOTDs with one marked default; doctrine managers attach MOTDs to doctrines and can make an attached MOTD public, so all members who can see the doctrine can copy it. Wherever an MOTD is shown it links back to Fleet Setup.
- **Apply Structure**: visual fleet structure tree (wings, squads, free-move flag) with an MOTD picker. One click pushes the structure and the chosen MOTD to your active fleet via ESI. Shows last-applied time per template. Anyone with Fleet Setup access can apply. Structures linked to "Visible to all members" doctrines can also be applied by any member with `basic_access` from the Doctrines page, with that doctrine's own MOTDs, useful for home defence or standing fleet ops.

### For Officers
- **Doctrine and fit management**: create, edit, archive, and restore doctrines. Add fits with EFT paste; organise them into skill tiers per doctrine. Move or copy fits between doctrines.
- **Pinned doctrines**: a Pinned checkbox on the doctrine form puts a doctrine in a Pinned section at the top of every doctrine list (Doctrines, My Skills, Fleet Command, Fleet Setup, Manage). Pinned doctrines keep their Sort Order among themselves, and every list, dropdown and the dashboard widget uses the same order: pinned, then Sort Order, then name.
- **Tag colours**: the doctrine form has a colour picker for the tag badge, with swatches, a hex field and a live preview.
- **Fleet structure templates** (Fleet Setup): build wing/squad layouts with a visual tree editor, set the free-move flag, and save as a named template. Anyone with Fleet Setup access can make structures and edit or delete their own; doctrine managers can edit any and link structures to doctrines. FCs then apply templates to their active fleet in one click.
- **Fit versioning**: every fit save is logged with a diff so you can track what changed and when.
- **Visibility control**: per-doctrine group picker for both member-facing visibility and MOTD access. Supports SIG/squad gating out of the box.
- **Collapsed by default**: per-doctrine toggle in the doctrine edit form, and a matching per-template toggle in the fleet structure form, controlling whether a doctrine card or structure's wing/squad tree starts open or closed. Applies everywhere doctrines or structures are listed (Doctrines, My Skills, Fleet Command, Fleet Setup, Manage). Defaults to collapsed.
- **Admin configuration**: tier badge thresholds, doctrine visibility defaults, and MOTD quick links are all configurable from the Django admin without touching code.

## Requirements

- Alliance Auth 5.0+ (Django 5.2, Python 3.10+)
- [aa-memberaudit](https://github.com/GoosefleetEO/aa-memberaudit) 4.0+
- [django-eveuniverse](https://github.com/GoosefleetEO/django-eveuniverse) 1.0+

## Installation

### 1. Install the package

```bash
pip install aa-fleet-hub
```

Or from source:

```bash
pip install -e /path/to/aa-fleet-hub
```

### 2. Add to INSTALLED_APPS

In your Alliance Auth `local.py`:

```python
INSTALLED_APPS += [
    "aa_fleet_hub",
]
```

### 3. Add the optional SSO callback (recommended)

Fleet Hub ships a wrapper around django-esi's SSO callback that prevents hard 500 errors when a browser drops its session cookie during the EVE login redirect, or when a user reloads an expired one-time callback link. Add this line **before** the `allianceauth.urls` include in your root `urls.py`:

```python
from aa_fleet_hub import views as fleet_hub_views
path("sso/callback/", fleet_hub_views.sso_callback),
```

If you skip this step, EVE SSO still works; you just lose the graceful error handling.

### 4. Run migrations

```bash
python manage.py migrate aa_fleet_hub
```

### 5. Register the EVE SSO scopes

Fleet Command requires two ESI scopes for pushing fleet structures and reading active fleets. Add both to the EVE application at [developers.eveonline.com](https://developers.eveonline.com):

- `esi-fleets.read_fleet.v1`
- `esi-fleets.write_fleet.v1`

If you want FCs to be able to save fits from the fit panel directly to their in-game fitting window, also add:

- `esi-fittings.write_fittings.v1`

### 6. Collect static files

```bash
python manage.py collectstatic --noinput
```

### 7. Restart services

Restart gunicorn and Celery worker/beat. On first boot the app will:

- Trigger an `eveuniverse` load of ships (category 6) and skills (category 16) if fewer than 400 of either are present
- Register two Celery beat tasks automatically (readiness cache update every [`FLEET_HUB_READINESS_INTERVAL`](#readiness-refresh-interval) minutes, hourly by default; weekly EVE type catalog refresh)

No manual task scheduling is required.

## Permissions

Assign permissions via Alliance Auth's Groups or the Permissions Tool. The five permissions are independent: grant only what each role needs.

| Permission | Intended role | What it unlocks |
|---|---|---|
| `aa_fleet_hub.basic_access` | All members | Doctrines page, My Skills, fit panel, dashboard widget |
| `aa_fleet_hub.fleet_command` | Fleet Commanders | Fleet Command page (readiness stats), Fleet Setup |
| `aa_fleet_hub.manage_fits` | FCs / fit managers | Add, edit, move, copy, and remove fits within any doctrine they can see; Fleet Setup |
| `aa_fleet_hub.manage_doctrines` | Officers | Full doctrine CRUD, archive/restore, all manage pages, fit management for all doctrines; in Fleet Setup, edit anyone's MOTDs and structures, attach them to doctrines, make MOTDs public |
| `aa_fleet_hub.view_member_assets` | Trusted leadership only | Ship ownership & hangar location columns on the Fleet Command readiness page |

A few things worth noting:

- `fleet_command` and `manage_fits` are independent. An FC who only flies can have `fleet_command` without edit rights; a fit manager can have `manage_fits` without Fleet Command access.
- **Fleet Setup** (make and copy MOTDs, make fleet structures, Apply Structure) is open to anyone with `fleet_command`, `manage_fits`, or `manage_doctrines`: running a fleet shouldn't require full readiness access. Each user edits or deletes only the MOTDs and structures they made; only `manage_doctrines` can edit anyone's, attach an MOTD or structure to a doctrine, or make an MOTD public.
- **Who sees an MOTD**: unattached MOTDs are visible to all Fleet Setup users. Attached ones follow the doctrine's "MOTD Visible To" groups, and a public one reaches every member who can see its doctrine. Doctrine managers see all of them, and everyone always sees their own.
- `manage_doctrines` is a superset of `manage_fits`: officers can do everything fit managers can, plus doctrine-level operations.
- Doctrine visibility is controlled separately per doctrine (group picker in the doctrine form), not by permissions. A member with `basic_access` only sees doctrines whose visibility groups they belong to, or doctrines marked "Visible to all members".
- **`view_member_assets` is intel-sensitive.** It reveals exactly which ships each member owns and *where they're hangared*; this is not a normal FC permission. Assign it only to a small, trusted leadership group, never broadly to FCs or officers. It has no effect outside the Fleet Command readiness page: it does not change what a member sees on the Doctrines page or in their own fit panel. Note that Django superusers see this data regardless of group membership (superusers automatically pass every permission check); treat superuser access on this instance with the same care as the permission itself.
- **CSV member roster export** (Character, Main/Alt, Corporation, Doctrine, Fit, Status, Owned Hulls, Locations) lives in Django Admin under **Fleet Hub Tools → Export Member Roster CSV**. It is superuser-only and has no separate permission gate; treat superuser access on this instance with the same care as `view_member_assets`, since the export contains the same ship-location data.

## Configuration

All settings are in the Django admin under **Fleet Hub → Fleet Hub Settings**. The settings page is a singleton: there is always exactly one row.

| Setting | Default | Description |
|---|---|---|
| Tier close max | 2 | Missing skills ≤ this shows the "N skills" (close) badge |
| Tier training max | 4 | Missing skills above close_max and ≤ this shows "Training" badge. Above this: "Not yet" |
| Doctrine default visible | Off | Pre-checks "Visible to all members" when creating a new doctrine |
| Dashboard widget enabled | On | Show the Fleet Readiness widget on the Alliance Auth home page. When off, the widget is hidden for all users regardless of their individual opt-in |

### Readiness refresh interval

The alliance-wide readiness on the Fleet Command page (and the dashboard's FC summary) is precomputed by a Celery beat task rather than on every page load. Set how often it runs in your Alliance Auth `local.py`:

```python
FLEET_HUB_READINESS_INTERVAL = 60  # minutes, default 60
```

Restart gunicorn and Celery worker/beat after changing it. The schedule is rebuilt from this setting on every start, so editing the task in Django admin under **Periodic Tasks** won't stick. Cached readiness is kept for twice the interval, so one late run doesn't leave Fleet Command empty.

The recompute also runs about 30 seconds after an officer saves a doctrine or fit (a burst of saves is collapsed into one run), and immediately from **Fleet Hub → Fleet Hub Tools → Recompute Readiness Now**. Skill syncs from memberaudit don't trigger it: a pilot who just trained a skill shows up on Fleet Command after the next scheduled run. A pilot's own Doctrines, My Skills and dashboard readiness are calculated live and always current.

### MOTD Quick Links

Under **Fleet Hub → MOTD Quick Links**, add links that appear in a dropdown in the MOTD toolbar when making or editing an MOTD in Fleet Setup. Each entry has:

- **Label**: name shown in the dropdown (e.g. `Standing Fleet`)
- **URL**: the full URL the link points to
- **Link text**: the text rendered inside the MOTD `<a>` tag
- **Order**: sort position in the dropdown (lower = higher)

The dropdown is hidden entirely if no quick links are configured.

### Authorizing Fleet Access (FCs)

The ESI fleet push (Apply Structure) requires each FC to authorize Fleet Hub against their own character: it is a per-FC credential, not a site-wide one. The authorization flow uses the same EVE SSO process as the rest of Alliance Auth.

**First time:**
1. Go to Fleet Command.
2. Click **Authorize Fleet Access** and complete the EVE SSO prompt.
3. The page returns to Fleet Command showing "Fleet Access Authorized".

**Re-authorization:** ESI tokens can expire or be revoked. If the Apply Structure button starts failing with auth errors, the FC should click **Re-authorize Fleet Access** to issue a fresh token.

FCs must be in an active fleet as fleet commander (not just fleet boss) when they click Apply Structure, or ESI will reject the write call.

## Initial skill data

Fleet Hub reads pilot skill data from `aa-memberaudit`. If a character shows zero skills after install, run the memberaudit update tasks from the Django admin (**Member Audit → Tools → Update All Characters**), or trigger an update for individual characters from the memberaudit character detail page.

After skills are updated, the readiness cache will refresh on the next scheduled run (hourly by default, see [Readiness refresh interval](#readiness-refresh-interval)), or you can trigger it immediately from **Fleet Hub → Fleet Hub Tools → Recompute Readiness Now**.

## Upgrading

```bash
pip install --upgrade aa-fleet-hub
python manage.py migrate aa_fleet_hub
python manage.py collectstatic --noinput
```

Then restart gunicorn and Celery.

To roll back a release, reinstall the previous version and restart. Don't migrate backwards unless that release's notes say it is safe: reversing the MOTD migration deletes every MOTD made since the upgrade.

While rolled back to 0.1.1 after upgrading to 0.2.0, pages, fits, fleet structures and doctrine edits work as before, with these limits:

- A doctrine that has an MOTD can't be deleted (archive it instead), and neither can a user who created an MOTD. Both work again after upgrading.
- MOTD edits made on 0.1.1 are not carried over when you upgrade again: 0.2.0 keeps its own copy of each MOTD from the first upgrade.
- Tag badges show without their colour. The colours come back after upgrading. A doctrine edited or created on 0.1.1 comes back with the 0.1.1 colour picked for it (green, blue, gold, red or purple).

## License

MIT, see [LICENSE](LICENSE).
