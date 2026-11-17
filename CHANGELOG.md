# Changelog

## [0.2.0] - 2026-11-15
6 features · 7 improvements · 19 bug fixes

### Features

1. Fleet Command's doctrine readiness page now hides alts and characters who can't fly a fit by default, with "Show alts" / "Show who can't fly" controls and a per-fit count of what's hidden ("Show 203 who can't fly", "4 alts hidden"). Mains are never hidden by the alt filter, and characters with no linked owner count as mains. The choice is remembered per user across sessions and devices. Hidden rows are left out of the page by the server, so a large doctrine's page went from about 4.2 MB and 1 second to about 340 KB and 0.16 seconds in testing (600 characters, 8 fits). Fits now start collapsed, showing their tier counts and most common missing skills, and expand to the character list on click
2. Fleet Command readiness counts now show pilots alongside characters (e.g. "12 characters / 5 pilots"), on the Fleet Command index, each doctrine's readiness page and the dashboard FC summary. A pilot is one owner with all their alts, counted once at the best tier any of their characters reaches; a character with no linked owner counts as its own pilot
3. Doctrines page search: a search box finds doctrines by ship name, with Tag, Ship class and Role dropdowns to narrow further (any selection within a dropdown, all dropdowns combined). A matching doctrine still shows all its fits, with the matching ones highlighted. Ship class comes from EVE's market groups (Frigates, Cruisers, Capital Ships...), and roles come from the fits' own Role field, so "Logi", "logi" and "LOGI" are one option. Active filters show as removable chips and are kept in the page URL, so a filtered view can be shared or bookmarked
4. Pinned doctrines: a Pinned checkbox on the doctrine form puts a doctrine in its own Pinned section at the top of the Doctrines page, My Skills, Fleet Command, Fleet Setup and Manage. Pinned doctrines keep their Sort Order among themselves, and every doctrine list (including dropdowns and the dashboard widget) now uses one shared order: pinned, then Sort Order, then name. Tag filters and search still find pinned doctrines, and duplicating a doctrine doesn't copy Pinned
5. Doctrine tags can be any colour: the doctrine form has a colour picker with the five old colours as swatches, a colour box and a hex field, plus a live badge preview. Badge text is black or white, whichever meets WCAG contrast on that colour, and every page draws the badge the same way. Colours are stored as `#rrggbb` and checked again on display. Duplicating a doctrine now keeps its tag colour
6. MOTDs are their own records instead of a text box on the doctrine. A doctrine can have several MOTDs with one marked default, so a second MOTD no longer needs a duplicate doctrine, and an MOTD can also stand alone. Fleet Setup lists every MOTD you can see, with search over name and text, a doctrine filter (including Standalone), Copy and Show text. Anyone with Fleet Setup access can make MOTDs and fleet structures and edit or delete their own; doctrine managers can edit any, attach them to doctrines and make an attached MOTD public (seen by all members). Unattached MOTDs are visible to all Fleet Setup users, and attached ones follow the doctrine's "MOTD visible to" groups. Apply Structure has an MOTD picker (the doctrine's MOTDs first, then standalone, then other doctrines'), and the Doctrines page lists each doctrine's MOTDs with a link back to Fleet Setup. Duplicating a doctrine copies its MOTDs. A migration turns each doctrine's MOTD into its default MOTD record, public for "visible to all members" doctrines. The old field is kept, unused, for this release so the previous version still works if reinstalled; a later release removes it. The migration was dry-run on staging with every MOTD restored byte for byte. Roll back by reinstalling the previous version, not by migrating backwards: reversing the migration deletes MOTDs made since the upgrade

### Improvements

1. PI plans in the fit panel now collapse per-plan (accordion, all collapsed by default) instead of switching via tabs: multiple plans (e.g. separate Robotics/Fuel Block setups) can be expanded side by side instead of hiding all but one
2. Hull ownership tooltip now dedupes hangar locations and shows a stacked-ship count (e.g. "Jita IV-4 (×3)") instead of listing the same station once per owned hull
3. Fleet Command's readiness cache is now split per doctrine plus a counts-only summary: opening one doctrine's readiness page loads only that doctrine's data, and the Fleet Command index and dashboard FC summary no longer load every character row for every fit just to show totals. Entries for doctrines that are archived or deactivated are removed on the next run, and the old single-entry cache is cleaned up automatically after upgrading
4. Doctrines and My Skills reworked for newer pilots: a "How to read this page" key, tag headings with counts, a card per fit with a "Can you fly it?" line, and plain badge words (Ready, 1 or 2 skills, Training, Not yet). My Skills gains a "Closest to qualifying" banner, collapsible tiers and an "Only show skills I'm missing" filter. Fleet Hub's section links moved into Alliance Auth's top bar, replacing the in-page subnav. Headers are real buttons for screen readers, focus rings are visible, and skill text meets WCAG AA on light and dark themes
5. The Doctrines page Link button now copies a link that opens just that doctrine (expanded, with a "Show all doctrines" link back), instead of scrolling the full page. Older links already pasted in chat open the new view. A doctrine that was removed, or that the viewer can't access, shows the same "Doctrine not found" message, so a link never reveals a doctrine to someone who can't see it
6. Fleet Setup is now the one place to make fleet structures and MOTDs, with + New Fleet Structure and + New MOTD buttons; Manage is doctrines and fits only. Old Manage structure links redirect to Fleet Setup, structures on archived or inactive doctrines stay reachable under "Other Fleet Structures" for their owner and doctrine managers, the structure form has its own Delete button, and form pages show breadcrumbs (e.g. Fleet Setup › New MOTD)
7. Requires Alliance Auth 5.0 or later (Django 5.2, Python 3.10+), up from 4.0. New columns in this release now carry a database default, so reinstalling 0.1.1 after upgrading still lets officers create doctrines and members turn on the dashboard widget. A fresh install no longer logs a Celery beat traceback during its first `migrate`, and fits whose EFT header gets the ship name added keep their original line breaks. See README, Upgrading, for what works while rolled back

### Bug Fixes

1. Fleet Command's per-doctrine drill-down page rendered a silently empty page (no message) for a doctrine with zero fits; it now shows the same "No fits in this doctrine." / "No skill requirements set up yet…" messaging already used on the Fleet Command index page
2. Dashboard widget's FC summary (alliance-wide ready/not-ready pilot counts per doctrine) was visible to any user with `manage_fits`, even without doctrine-level access; it now requires `fleet_command` or `manage_doctrines`, matching the intent that org-wide readiness numbers are doctrine-access data, not fit-editing data
3. `fit_create` allowed blank fit names; it now rejects them with the same validation `fit_edit` already had
4. A fit tier with zero configured skills was scored as "0 missing" (green/ready) instead of being treated as not ready; it now forces the "far" badge and is excluded from a fit's "best tier" calculation
5. Fleet Command's readiness cache was deleted on every character's skill sync and unconditionally recomputed by anyone loading the page while it was empty, bypassing the existing debounce lock; on a large alliance with characters syncing continuously throughout the day this caused frequent, overlapping full recomputes (visible as the page recomputing far more often than the intended hourly refresh, and elevated server load). It now keeps serving the last cached data until a debounced recompute finishes instead of clearing the cache upfront, and the page's own empty-cache fallback goes through the same debounced path
6. **Security:** the dashboard widget's FC summary read every active doctrine straight from the readiness cache without checking doctrine visibility, so an FC without group access to a group-restricted doctrine could still see its name and ready/not-ready pilot counts on their dashboard; it's now filtered through the same visibility check used everywhere else
7. Dashboard widget's per-doctrine "ready" count divided by the total number of fits (including fits with no skill plan) but only summed over fits that actually have one, understating readiness for any doctrine mixing tiered and no-skill-plan fits; it now divides by the same set of fits it sums over
8. Fleet Command's per-doctrine drill-down page didn't request a recompute when it hit a cold readiness cache (e.g. a deep link visited before anyone had loaded the Fleet Command index), leaving it stuck showing "not yet computed" until the next hourly refresh; it now queues a recompute through the same debounced path the index page uses
9. Applying a fleet structure template ran the wing/squad creation as dozens of sequential, synchronous ESI calls inside the request itself; a large template (many wings x squads) could take long enough to outrun the web server's request timeout even though the fleet was actually being restructured correctly. It now runs as a background task, so the request returns immediately once the fleet/permission checks pass
10. Fleet structure wing/squad rename calls didn't check the ESI response status, so a failed rename was silently ignored and still counted toward "Applied: N wing(s)..." in the success message; a failed rename is now logged and no longer counted as applied
11. Fleet Command's readiness recomputed far more often than its hourly schedule: every successful memberaudit skill sync queued a recompute (in practice about every 30 seconds on a large alliance), each doctrine/fit save queued its own undebounced full recompute, and the interval was hardcoded so an edit in Django admin was reset on restart. Skill syncs no longer trigger a recompute (the scheduled run picks them up), doctrine/fit saves go through the 30-second debounce, and the interval is set with the new `FLEET_HUB_READINESS_INTERVAL` setting (minutes, default 60; cache kept for twice the interval). A pilot's newly trained skills now show on Fleet Command after the next scheduled run; their own Doctrines, My Skills and dashboard readiness stay live
12. Fits saved with an EFT header missing the ship (`[Fit Name]` instead of `[Ship, Fit Name]`) couldn't be imported into EVE; the header is now repaired on save, existing fits are fixed by a migration, and a header that's still wrong or appears twice is rejected with a clear message. Any error on the fit form now keeps everything you typed instead of clearing the form
13. Darkly theme: panels, headers and borders (including the dashboard widget and the "Viewing as" select) rendered white with unreadable text; they now use Darkly's dark surfaces
14. The MOTD editor's preview didn't match the game: links showed blue instead of their font colour, default text was cyan, faded colours dimmed whole blocks, `>>Fits&lt;&lt;` lost its symbols, italic or colour-only text was dropped, plain line breaks (which the game shows) were ignored, links were underlined, and size-14 text looked smaller than untagged text. The preview now reads the markup the way the game shows it, and builds the preview from text instead of inserting HTML
15. MOTD text could change between saving and pasting in game: saving trimmed spaces and blank lines at the start and end, and Copy took the text from the page, where the browser turns Windows line breaks into plain ones. MOTDs are now saved and copied exactly as written, apart from line breaks: the browser sends every line break in the editor as a Windows one, which shows the same in game
16. Duplicating a doctrine left every copied fit's Notes blank; the copy now keeps them, and duplicate fit, duplicate doctrine and move/copy share one copy step so they can't drift apart again
17. Fleet Command readiness kept showing the old numbers for up to the scheduled interval after duplicating a doctrine or fit, moving or copying a fit, archiving or restoring a doctrine, or deleting a doctrine or fit; each of these now queues the same debounced recompute a doctrine or fit save does
18. A duplicate doctrine or fit copy that failed partway (for example a database error) could leave a half-made copy behind, such as a doctrine with only some of its fits; each copy now saves all at once or not at all
19. **Security:** Apply Structure didn't check that the structure's doctrine was visible to the user, so a fleet structure on a group-restricted doctrine could be pushed by its id: by an FC outside the doctrine's groups, or by any member if the doctrine was "visible to all members", even after it was archived. Apply Structure now only works on doctrines the user can see, the same set Fleet Setup lists; standalone structures and an FC's own structures are unchanged

## [0.1.1] - 2026-07-10
3 features · 8 improvements · 9 bug fixes

### Features

1. Manual sort order for fits within a doctrine (matches the existing doctrine sort order field): officers set a number per fit in the fit edit form to control display order on the Doctrines page, Fleet Command, and My Skills
2. New `aa_fleet_hub.view_member_assets` permission (intel-sensitive, trusted leadership only): adds ship ownership & hangar location columns to the Fleet Command readiness page. Django superusers see this data regardless of group membership. See README Permissions section before granting it.
3. CSV member roster export in Django Admin (Fleet Hub Tools → Export Member Roster CSV, superuser-only): one row per character × doctrine × fit, with Main/Alt flag, corporation, status, owned hull count, and hangar locations

### Improvements

1. Move/copy fit page restyled to match the rest of the Manage pages (Fleet Hub theming, subnav, draft-doctrine tag in the target dropdown, copy vs. move hint text)
2. Fleet Command readiness table is now sortable by Name / Status / Corporation, and groups each Main with their Alts instead of listing every character flat
3. Doctrines page and Manage page now group doctrines into sections by their tag (sorted alphabetically, "Untagged" always last), with a filter bar (All + one button per tag) to show only one category at a time
4. Fit panel Skills tab no longer shows the Copy EFT / Copy Buy All / Save to ESI buttons: they only apply to the Fit tab and were confusing next to skill pip data
5. Per-doctrine "Collapsed by default" option in the doctrine edit form: officers control whether each doctrine's card starts open or closed on the Doctrines and Manage pages. Defaults to collapsed for both new doctrines and existing ones (backfilled by the migration), replacing the previous behavior where every card was always open on page load.
6. Tag filter bar and grouping (previously Doctrines + Manage only) now also on My Skills, Fleet Command, and Fleet Setup: same alphabetical grouping with "Untagged" last, same filter pills
7. "Collapsed by default" now also respected on My Skills, Fleet Command, and Fleet Setup (previously only Doctrines and Manage checked the doctrine's setting; the other three pages always forced cards open regardless)
8. New per-template "Collapsed by default" option in the fleet structure edit form: controls whether a structure's wing/squad tree starts open or closed on the Manage and Fleet Setup pages (structure templates had no such setting before; their trees, including the standalone structures list, always forced open)

### Bug Fixes

1. PI plan icon showed the wrong image for Storm and Temperate planet types (mapped to unrelated EVE item type IDs instead of the correct planet type IDs)
2. Move/copy fit dropdown excluded non-active (WIP) doctrines as valid targets, preventing fit transfers between draft doctrines
3. Fits with no skill plan were silently omitted from the Fleet Command readiness table instead of being shown as not-ready; they now display a red "No Skill Plan" badge instead of defaulting to green/absent
4. Doctrines page pip legend's "Below requirement" example showed two gold (short) pips and three red (unmet) pips, but `pip_states()` only ever produces one gold pip (at the exact required level); everything below it is unmet. Legend corrected to unmet, unmet, short, neutral, neutral with a caption clarifying gold = next skill needed, red = further out
5. "Collapse All" button on Doctrines, My Skills, Fleet Command, Fleet Setup, and Manage always started labeled "Collapse All" even when cards were already collapsed by default, so the first click silently did nothing instead of expanding; button label now syncs with actual on-load card state
6. Doctrine/MOTD group picker built its selected-tag chips with unescaped `innerHTML`, so a Django group name containing HTML would render as markup instead of text; now built with safe DOM methods, matching the rest of the picker
7. Structures & MOTD page re-fetched `motd_groups` and `fleet_structures` a second time on every load due to a redundant `prefetch_related()` already covered by the underlying doctrine query
8. A burst of skill-sync completions (e.g. running "Update All Characters" in memberaudit admin) could queue one full alliance-wide readiness recompute per character instead of one; recompute requests are now debounced to a single queued run
9. If pushing a fleet structure via ESI failed partway through creating wings, any wings already created in that attempt were left behind in the live fleet instead of being cleaned up

## [0.1.0] - 2026-06-29
23 features

### Features

1. Doctrines page with collapsible cards, ship rows, role badges, fit notes, and a Collapse/Expand All button
2. Fit panel (AJAX slide-in) with Fit tab (EFT + PI templates) and Skills tab (pip states: trained/short/unmet/queued/exceed, target-pip ring)
3. Fit panel actions: Copy EFT, Copy Buy All (client-side, full ship + module quantities), Save to ESI (pushes fit to in-game fitting window)
4. My Skills page with per-doctrine skill grids broken down by tier, collapsible tier rows, and a closest-to-qualifying banner
5. Fleet Command page with alliance-wide readiness stats, per-doctrine pilot counts by tier
6. MOTD builder with EVE XML live preview, toolbar (size/bold/italic/underline/colour/link/channel inserts), and a configurable quick-link dropdown
7. Apply Structure ESI push (wings, squads, free-move flag, MOTD) from saved fleet structure templates; shows last-applied time per template (localStorage, 30 min TTL)
8. Fleet structure template builder (visual wing/squad tree, free-move toggle, MOTD field)
9. Hull ownership indicator on ship portraits (green glow = in hangar, greyscale = not owned); hover tooltip shows station name(s)
10. Character switcher ("Viewing as") in the sub-navigation bar; readiness, skill pips, and hull ownership all follow the selected character
11. Dashboard widget (opt-in per user) showing personal readiness summary; global kill-switch in admin settings
12. Doctrine visibility control: per-doctrine group picker plus "Visible to all members" flag for home-defence / standing-fleet doctrines
13. Self-service Apply Structure for "Visible to all members" doctrines (any member with `basic_access` can apply)
14. MOTD group gating: per-doctrine group picker controls who sees the MOTD builder
15. Fit versioning with change log (diff per save); version badge in fit panel and manage page
16. Archive and restore for doctrines
17. Doctrine and fit CRUD fully accessible from the Manage page; move/copy fits between doctrines
18. Hourly readiness cache update and weekly EVE type catalog refresh registered automatically as Celery beat tasks on app startup
19. "Recompute Readiness Now" and "Refresh Ship/Skill Catalog" buttons in Django admin (Fleet Hub Tools)
20. `fleethub_update_readiness` and `fleethub_refresh_eve_types` management commands for on-demand runs
21. `FleetHubSettings` singleton in Django admin: tier badge thresholds, doctrine visibility default, dashboard widget kill-switch
22. `MotdQuickLink` model in admin: label/URL/link text/order; dropdown hidden if no entries configured
23. Hardened SSO callback wrapper to prevent 500 errors on browser session drops or expired one-time callback links
