/* Fleet Hub — fleet.js */

// ---------------------------------------------------------------------------
// Group picker — searchable tag UI for doctrine group selectors
// ---------------------------------------------------------------------------

function initGroupPicker(wrapperId, allGroups, selectedIds, fieldName) {
  const wrap = document.getElementById(wrapperId);
  if (!wrap) return;
  const box      = wrap.querySelector('.fh-gp-box');
  const tagsEl   = wrap.querySelector('.fh-gp-tags');
  const searchEl = wrap.querySelector('.fh-gp-search');
  const dropdown = wrap.querySelector('.fh-gp-dropdown');
  const selected = new Set(selectedIds.map(Number));

  function renderTags() {
    tagsEl.innerHTML = '';
    wrap.querySelectorAll('input[type=hidden][data-gp]').forEach(el => el.remove());
    selected.forEach(pk => {
      const g = allGroups.find(x => x.pk === pk);
      if (!g) return;
      const tag = document.createElement('span');
      tag.className = 'fh-gp-tag';
      tag.appendChild(document.createTextNode(g.name + ' '));
      const remove = document.createElement('span');
      remove.className = 'fh-gp-tag-remove';
      remove.dataset.pk = pk;
      remove.textContent = '×';
      tag.appendChild(remove);
      tagsEl.appendChild(tag);
      const inp = document.createElement('input');
      inp.type = 'hidden'; inp.name = fieldName; inp.value = pk; inp.dataset.gp = '1';
      wrap.appendChild(inp);
    });
  }

  function renderDropdown(q) {
    const lower = q.toLowerCase();
    const hits = allGroups.filter(g => g.name.toLowerCase().includes(lower) && !selected.has(g.pk));
    dropdown.innerHTML = '';
    if (!hits.length) {
      dropdown.innerHTML = '<div class="fh-gp-empty">No matches</div>';
    } else {
      hits.forEach(g => {
        const opt = document.createElement('div');
        opt.className = 'fh-gp-option';
        opt.textContent = g.name;
        opt.dataset.pk = g.pk;
        dropdown.appendChild(opt);
      });
    }
    dropdown.style.display = 'block';
  }

  box.addEventListener('click', () => searchEl.focus());
  searchEl.addEventListener('focus', () => renderDropdown(searchEl.value));
  searchEl.addEventListener('input', () => renderDropdown(searchEl.value));

  dropdown.addEventListener('mousedown', e => {
    const opt = e.target.closest('.fh-gp-option');
    if (!opt) return;
    e.preventDefault();
    selected.add(Number(opt.dataset.pk));
    searchEl.value = '';
    renderTags();
    dropdown.style.display = 'none';
  });

  tagsEl.addEventListener('click', e => {
    const btn = e.target.closest('.fh-gp-tag-remove');
    if (!btn) return;
    selected.delete(Number(btn.dataset.pk));
    renderTags();
    if (document.activeElement === searchEl) renderDropdown(searchEl.value);
  });

  document.addEventListener('click', e => {
    if (!wrap.contains(e.target)) dropdown.style.display = 'none';
  });

  renderTags();
}

// ---------------------------------------------------------------------------
// Fit panel
// ---------------------------------------------------------------------------

function fpSwitchTab(tab) {
  ['fit', 'skills', 'pi'].forEach(t => {
    const el    = document.getElementById('fp-content-' + t);
    const tabEl = document.getElementById('fp-tab-' + t);
    if (el)    el.style.display = (t === tab) ? '' : 'none';
    if (tabEl) tabEl.classList.toggle('active', t === tab);
  });
  // Copy EFT / Copy Buy All / Save to ESI only make sense for the Fit tab —
  // showing them on Skill Requirements was confusing officers reviewing readiness.
  ['fp-copy-btn', 'fp-buyall-btn', 'fp-esi-btn'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.style.display = (tab === 'fit') ? '' : 'none';
  });
}

function fpCopyEft() {
  const el  = document.getElementById('fp-eft-block');
  const btn = document.getElementById('fp-copy-btn');
  if (!el) return;
  navigator.clipboard.writeText(el.innerText).then(() => {
    if (btn) { btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = 'Copy EFT'; }, 1500); }
  });
}

function fpCopySkillPlan(btn) {
  // Aggregate unique missing skills (highest required level) across all tiers.
  const needed = new Map();
  document.querySelectorAll('#fp-content-skills .sg-name').forEach(el => {
    const name = el.dataset.skill;
    const req  = parseInt(el.dataset.req);
    const have = parseInt(el.dataset.have);
    if (have < req) {
      const existing = needed.get(name);
      if (!existing || req > existing.req) needed.set(name, { req, have });
    }
  });
  const lines = Array.from(needed.entries())
    .map(([name, { req, have }]) => `${name} ${ROMAN[req]}  (have ${ROMAN[have]})`);
  if (!lines.length) return;
  navigator.clipboard.writeText(lines.join('\n')).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  });
}

async function copyTierSkills(btn, url) {
  try {
    const res = await fetch(url);
    const text = await res.text();
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 1500);
  } catch(e) {
    btn.textContent = 'Failed';
    setTimeout(() => btn.textContent = btn.dataset.orig || 'Get', 1500);
  }
}

function fpCopyBuyAll(btn) {
  const el = document.getElementById('fp-eft-block');
  if (!el) return;
  const raw = el.innerText.trim().split('\n');
  const items = {};

  // Line 0: [Ship, Fit Name] — extract ship name
  const headerM = raw[0] && raw[0].match(/^\[(.+?),/);
  if (headerM) { const s = headerM[1].trim(); items[s] = (items[s] || 0) + 1; }

  // Split remainder into groups by blank lines; classify by content
  const groups = [];
  let cur = [];
  for (let i = 1; i < raw.length; i++) {
    const line = raw[i].trim();
    if (!line) { if (cur.length) { groups.push(cur); cur = []; } }
    else cur.push(line);
  }
  if (cur.length) groups.push(cur);

  for (const group of groups) {
    const isQty = group.every(l => /^.+\s+x\d+$/.test(l));
    if (isQty) {
      for (const l of group) {
        const m = l.match(/^(.+?)\s+x(\d+)$/);
        if (m) { const n = m[1].trim(); items[n] = (items[n] || 0) + parseInt(m[2]); }
      }
    } else {
      for (const l of group) {
        if (/^\[empty\s/i.test(l)) continue;
        const name = l.split(/\s*,\s*/)[0].trim(); // strip ammo suffix
        if (name) items[name] = (items[name] || 0) + 1;
      }
    }
  }

  const text = Object.entries(items).map(([n, q]) => `${n} x${q}`).join('\n');
  if (!text) return;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  });
}

async function fpSaveToEsi(btn, url) {
  const orig = btn.textContent;
  btn.textContent = 'Saving…';
  btn.disabled = true;
  const csrfToken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
  try {
    const res = await fetch(url, { method: 'POST', headers: { 'X-CSRFToken': csrfToken } });
    const data = await res.json();
    if (data.status === 'no_token') {
      window.location.href = data.authorize_url;
      return;
    }
    btn.textContent = data.status === 'ok' ? 'Saved!' : 'Error';
    btn.title = data.message || '';
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; btn.title = ''; }, 3000);
  } catch(e) {
    btn.textContent = 'Error';
    setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
  }
}

async function fpSwitchCharacter(characterId, fitId) {
  // Switch the active character and reload the fit panel to reflect new skill data.
  if (!window.FLEET_SWITCH_CHAR_URL) return;
  const csrfToken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
  try {
    const res = await fetch(window.FLEET_SWITCH_CHAR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': csrfToken },
      body: `character_id=${characterId}&next=/fleet/`,
    });
    if (res.ok) openFitPanel(fitId);
  } catch {
    // Network error — silently ignore, character switch is non-critical
  }
}

// ---------------------------------------------------------------------------
// Doctrine card collapse
// ---------------------------------------------------------------------------

// Doctrines and My Skills: each .fh-doc card opens from its header button,
// and clicking anywhere else on the header (except its own buttons) does the same.
function fhSetDocOpen(doc, open) {
  doc.classList.toggle('open', open);
  doc.querySelector('.fh-doc-toggle')?.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function fhSyncToggleAll() {
  const btn = document.getElementById('fh-toggle-all');
  if (!btn) return;
  const anyOpen = Array.from(document.querySelectorAll('.fh-doc:not([hidden])')).some(d => d.classList.contains('open'));
  btn.textContent = anyOpen ? 'Collapse all' : 'Expand all';
}

function fhToggleAll() {
  const docs = document.querySelectorAll('.fh-doc:not([hidden])');
  const anyOpen = Array.from(docs).some(d => d.classList.contains('open'));
  docs.forEach(d => fhSetDocOpen(d, !anyOpen));
  fhSyncToggleAll();
}

// "Show me" on the closest-to-qualifying banner: clear a tag filter hiding
// the fit, open its doctrine and scroll to it.
function fhShowFit(id) {
  const fit = document.getElementById(id);
  if (!fit) return;
  // A pinned doctrine (#6) can be hidden by the tag filter while its section shows.
  if (fit.closest('.fh-tag-group')?.style.display === 'none'
      || fit.closest('[data-doc-tag]')?.style.display === 'none') {
    document.querySelector('.fh-tag-filter-btn[data-tag-filter=""]')?.click();
  }
  const doc = fit.closest('.fh-doc');
  if (doc) fhSetDocOpen(doc, true);
  fhSyncToggleAll();
  fit.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.fh-doc-hdr').forEach(hdr => {
    hdr.addEventListener('click', e => {
      if (e.target.closest('a, textarea, select, button:not(.fh-doc-toggle)')) return;
      const doc = hdr.closest('.fh-doc');
      fhSetDocOpen(doc, !doc.classList.contains('open'));
      fhSyncToggleAll();
    });
  });
  document.querySelectorAll('.fh-tier-hdr').forEach(btn => {
    btn.addEventListener('click', () => {
      const tier = btn.closest('.fh-tier');
      tier.classList.toggle('closed');
      btn.setAttribute('aria-expanded', tier.classList.contains('closed') ? 'false' : 'true');
    });
  });
  // Display only, nothing saved. Synced on load too, since browsers can restore the tick without a change event.
  const onlyMissing = document.getElementById('fh-only-missing');
  if (onlyMissing) {
    const syncOnlyMissing = () => onlyMissing.closest('.fh-page')?.classList.toggle('only-missing', onlyMissing.checked);
    onlyMissing.addEventListener('change', syncOnlyMissing);
    syncOnlyMissing();
  }
  // Remember a folded "How to read this page" per browser.
  const key = document.querySelector('details.fh-key');
  if (key) {
    try { if (localStorage.getItem('fh_key_open') === '0') key.open = false; } catch (e) {}
    key.addEventListener('toggle', () => {
      try { localStorage.setItem('fh_key_open', key.open ? '1' : '0'); } catch (e) {}
    });
  }
  fhSyncToggleAll();
});

// Link to the single-doctrine view (#17).
function copyDoctrineLink(doctrineId) {
  const url = window.location.origin + window.location.pathname + '?doctrine=' + doctrineId;
  navigator.clipboard.writeText(url).then(() => {
    const btn = document.querySelector(`[data-copy-link="${doctrineId}"]`);
    if (btn) { const orig = btn.textContent; btn.textContent = 'Copied!'; setTimeout(() => { btn.textContent = orig; }, 1500); }
  });
}

// Old #doctrine-<id> links (from before #17, already pasted in Discord) open
// the single-doctrine view. The server picks the doctrine, so this reloads.
document.addEventListener('DOMContentLoaded', function() {
  const m = window.location.hash.match(/^#doctrine-(\d+)$/);
  if (!m || !document.querySelector('[data-fh-page="doctrines"]')) return;
  window.location.replace(window.location.pathname + '?doctrine=' + m[1]);
});

// ---------------------------------------------------------------------------
// Generic [data-fc-collapse] toggle — used for Fleet Structure wing/squad
// trees on both the Doctrines and Fleet Command pages.
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('[data-fc-collapse]').forEach(function(header) {
    header.addEventListener('click', function() {
      var body = document.getElementById(this.getAttribute('data-fc-collapse'));
      if (!body) return;
      var isOpen = body.style.display !== 'none';
      body.style.display = isOpen ? 'none' : '';
      var arrow = this.querySelector('.doctrine-arrow');
      if (arrow) arrow.classList.toggle('open', !isOpen);
    });
  });
});

// ---------------------------------------------------------------------------
// Copy MOTD / Apply Fleet Structure — shared by Doctrines and Fleet Command
// ---------------------------------------------------------------------------

// MOTD text comes from the page's fh-motd-texts JSON, not from HTML: the
// HTML parser turns CRLF into LF and drops a leading newline, and the text
// must reach the game exactly as saved (#3).
function copyMotd(btn, motdId) {
  const el = document.getElementById('fh-motd-texts');
  if (!el) return;
  const text = JSON.parse(el.textContent)[motdId];
  if (text === undefined) return;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 1500);
  });
}

function _structLastAppliedKey(structId) { return 'fh_struct_applied_' + structId; }

function _renderStructStatus(structId) {
  const el = document.getElementById('struct-status-' + structId);
  if (!el) return;
  const raw = localStorage.getItem(_structLastAppliedKey(structId));
  if (!raw) { el.textContent = ''; return; }
  try {
    const { ts, wings } = JSON.parse(raw);
    const age = Math.floor((Date.now() - ts) / 60000);
    if (age >= 30) { localStorage.removeItem(_structLastAppliedKey(structId)); el.textContent = ''; return; }
    const when = age < 1 ? 'just now' : age + ' min ago';
    el.textContent = 'Last applied: ' + wings + ' wing' + (wings !== 1 ? 's' : '') + ' · ' + when;
  } catch(e) { el.textContent = ''; }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-struct-id]').forEach(btn => {
    _renderStructStatus(btn.dataset.structId);
  });
});

async function applyStructure(btn, applyUrl) {
  const orig = btn.textContent;
  btn.textContent = 'Applying…';
  btn.disabled = true;
  try {
    const csrfToken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
    // The MOTD picker beside the button, if any. Without one the server
    // pushes the doctrine's default MOTD.
    const body = new FormData();
    const pick = btn.dataset.motdSelect && document.getElementById(btn.dataset.motdSelect);
    if (pick) body.append('motd_id', pick.value);
    const res = await fetch(applyUrl, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken },
      body,
    });
    const data = await res.json();
    if (data.status === 'ok') {
      btn.textContent = 'Applied!';
      btn.style.color = 'var(--fh-success, #4caf6b)';
      const structId = btn.dataset.structId;
      if (structId) {
        localStorage.setItem(_structLastAppliedKey(structId), JSON.stringify({
          ts: Date.now(),
          wings: parseInt(btn.dataset.wings || 0, 10),
        }));
        _renderStructStatus(structId);
      }
    } else if (data.message && data.message.toLowerCase().includes('re-authorize')) {
      btn.textContent = 'Authorize Fleet Access →';
      btn.style.color = 'var(--fh-danger, #e05252)';
      btn.disabled = false;
      btn.onclick = function() { window.location.href = window.FLEET_AUTHORIZE_URL; };
      return;
    } else {
      btn.textContent = data.message || 'Failed';
      btn.style.color = 'var(--fh-danger, #e05252)';
    }
  } catch(e) {
    btn.textContent = 'Error';
    btn.style.color = '#e05252';
  }
  setTimeout(() => {
    btn.textContent = orig;
    btn.disabled = false;
    btn.style.color = '';
  }, 2500);
}

// ---------------------------------------------------------------------------
// Expand / Collapse All doctrines
// ---------------------------------------------------------------------------

// Sync each "Collapse All" button's initial label with the actual on-load
// state of its cards, since collapsed_by_default can leave some or all of
// them already collapsed when the page renders.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.fh-collapse-all-btn[data-collapse-target]').forEach(btn => {
    const bodies = document.querySelectorAll(btn.dataset.collapseTarget);
    if (bodies.length === 0) return;
    const anyOpen = Array.from(bodies).some(b => b.style.display !== 'none');
    btn.textContent = anyOpen ? 'Collapse All' : 'Expand All';
  });
});

// Section header count, e.g. "2 of 5 doctrines" while a filter hides some.
function fhSetGroupCount(group, shown) {
  const count = group.querySelector(':scope > h2 .count');
  if (!count) return;
  const total = Number(count.dataset.total);
  count.textContent = (shown < total ? shown + ' of ' : '')
    + total + ' doctrine' + (total === 1 ? '' : 's');
}

function filterDoctrineTag(btn, tag) {
  const bar = btn.closest('.fh-tag-filter-bar');
  bar.querySelectorAll('.fh-tag-filter-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
    if (b.hasAttribute('aria-pressed')) b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
  });
  document.querySelectorAll('.fh-tag-group').forEach(group => {
    if (!group.hasAttribute('data-pinned')) {
      group.style.display = (!tag || group.dataset.tagGroup === tag) ? '' : 'none';
      return;
    }
    // Pinned doctrines (#6) keep their own tag, so filter them one by one.
    let shown = 0;
    group.querySelectorAll('[data-doc-tag]').forEach(doc => {
      const show = !tag || doc.dataset.docTag === tag;
      doc.style.display = show ? '' : 'none';
      if (show) shown++;
    });
    group.style.display = shown ? '' : 'none';
    fhSetGroupCount(group, shown);
  });
}

// ---------------------------------------------------------------------------
// Doctrine search (#4): ship name search plus multi-select Tag, Ship class and
// Role. OR within a facet, AND between facets. Ship name, class and role match
// per fit, so a doctrine shows (whole, with its matching fits highlighted) when
// at least one fit passes all three. The state lives in the URL query string.
// Each facet is a <details> dropdown of checkboxes. Role values are lowercased
// keys; chips show the checkbox label instead.
// ---------------------------------------------------------------------------

const fhFilter = { q: '', tag: new Set(), class: new Set(), role: new Set() };
const FH_FACETS = ['tag', 'class', 'role'];
const FH_FACET_LABELS = { tag: 'Tag', class: 'Ship class', role: 'Role' };
// Cards open before a fit filter started, restored when it ends.
let fhOpenBeforeFilter = null;
let fhUrlTimer = null;

function fhFacetLabel(facet, value) {
  const box = Array.from(document.querySelectorAll(`.fh-facet[data-facet="${facet}"] input`))
    .find(b => b.dataset.value === value);
  return box ? box.closest('label').textContent.trim() : value;
}

function fhOrList(values) {
  const v = Array.from(values);
  return v.length < 2 ? v.join('') : v.slice(0, -1).join(', ') + ' or ' + v[v.length - 1];
}

function fhFilterEmptyText(q) {
  if (!q && !fhFilter.class.size && !fhFilter.role.size) return 'No doctrine is tagged ' + fhOrList(fhFilter.tag) + '.';
  let text = 'No doctrine';
  if (fhFilter.tag.size) text += ' tagged ' + fhOrList(fhFilter.tag);
  text += ' has a fit';
  if (q) text += ' matching "' + q + '"';
  if (fhFilter.role.size) text += ' with role ' + fhOrList(Array.from(fhFilter.role, v => fhFacetLabel('role', v)));
  if (fhFilter.class.size) text += ' in ' + fhOrList(fhFilter.class);
  return text + '.';
}

function fhFilterChip(label, onRemove) {
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'fh-chip';
  chip.setAttribute('aria-label', 'Remove filter ' + label);
  chip.textContent = label + ' ';
  const x = document.createElement('span');
  x.setAttribute('aria-hidden', 'true');
  x.textContent = '×';
  chip.appendChild(x);
  chip.addEventListener('click', () => {
    const index = Array.prototype.indexOf.call(chip.parentNode.children, chip);
    onRemove();
    const chips = document.getElementById('fh-filter-chip-list').children;
    (chips[Math.min(index, chips.length - 1)] || document.getElementById('fh-ship-search')).focus();
  });
  return chip;
}

function fhFilterApply() {
  const root = document.getElementById('fh-doctrine-filter');
  if (!root) return;
  const q = fhFilter.q.trim();
  const needle = q.toLowerCase();
  const fitFilter = needle !== '' || fhFilter.class.size > 0 || fhFilter.role.size > 0;
  let shown = 0, total = 0;
  if (fitFilter && !fhOpenBeforeFilter) {
    fhOpenBeforeFilter = new Set(document.querySelectorAll('.fh-doc.open'));
  }

  document.querySelectorAll('.fh-tag-group').forEach(group => {
    let groupShown = 0;
    group.querySelectorAll('.fh-doc').forEach(doc => {
      total++;
      const tagOk = !fhFilter.tag.size || fhFilter.tag.has(doc.dataset.docTag);
      let matches = 0;
      doc.querySelectorAll('.fh-fit').forEach(fit => {
        const hit = fitFilter
          && (!needle || (fit.dataset.shipName || '').toLowerCase().includes(needle))
          && (!fhFilter.class.size || fhFilter.class.has(fit.dataset.shipClass))
          && (!fhFilter.role.size || fhFilter.role.has(fit.dataset.role));
        fit.classList.toggle('fh-match', hit);
        if (hit) matches++;
      });
      const visible = tagOk && (!fitFilter || matches > 0);
      doc.hidden = !visible;
      if (visible) groupShown++;
      const isMatch = visible && fitFilter;
      if (isMatch && doc.dataset.fhMatch !== '1') fhSetDocOpen(doc, true);
      doc.dataset.fhMatch = isMatch ? '1' : '';
    });
    group.hidden = groupShown === 0;
    shown += groupShown;
    fhSetGroupCount(group, groupShown);
  });
  if (!fitFilter && fhOpenBeforeFilter) {
    document.querySelectorAll('.fh-doc').forEach(doc => fhSetDocOpen(doc, fhOpenBeforeFilter.has(doc)));
    fhOpenBeforeFilter = null;
  }
  fhSyncToggleAll();

  root.querySelectorAll('.fh-facet').forEach(facet => {
    const selected = fhFilter[facet.dataset.facet];
    facet.querySelectorAll('input').forEach(box => { box.checked = selected.has(box.dataset.value); });
    facet.querySelector('.fh-facet-count').textContent = selected.size ? ' (' + selected.size + ')' : '';
  });

  const chipList = document.getElementById('fh-filter-chip-list');
  chipList.replaceChildren();
  if (q) {
    chipList.appendChild(fhFilterChip('Ship: ' + q, () => {
      fhFilter.q = '';
      document.getElementById('fh-ship-search').value = '';
      fhFilterApply();
    }));
  }
  FH_FACETS.forEach(facet => {
    fhFilter[facet].forEach(value => {
      chipList.appendChild(fhFilterChip(FH_FACET_LABELS[facet] + ': ' + fhFacetLabel(facet, value), () => {
        fhFilter[facet].delete(value);
        fhFilterApply();
      }));
    });
  });
  const active = chipList.children.length > 0;
  document.getElementById('fh-filter-chips').hidden = !active;

  const empty = document.getElementById('fh-filter-empty');
  empty.hidden = !(active && shown === 0);
  if (!empty.hidden) document.getElementById('fh-filter-empty-text').textContent = fhFilterEmptyText(q);
  document.getElementById('fh-filter-status').textContent =
    active ? 'Showing ' + shown + ' of ' + total + ' doctrine' + (total === 1 ? '' : 's') : '';

  clearTimeout(fhUrlTimer);
  fhUrlTimer = setTimeout(fhFilterWriteUrl, 250);
}

function fhFilterWriteUrl() {
  const params = new URLSearchParams(window.location.search);
  ['q', ...FH_FACETS].forEach(k => params.delete(k));
  const q = fhFilter.q.trim();
  if (q) params.set('q', q);
  FH_FACETS.forEach(facet => fhFilter[facet].forEach(v => params.append(facet, v)));
  const qs = params.toString();
  history.replaceState(null, '', window.location.pathname + (qs ? '?' + qs : '') + window.location.hash);
}

function fhFilterClear() {
  fhFilter.q = '';
  FH_FACETS.forEach(facet => fhFilter[facet].clear());
  document.getElementById('fh-ship-search').value = '';
  fhFilterApply();
}

document.addEventListener('DOMContentLoaded', () => {
  const root = document.getElementById('fh-doctrine-filter');
  if (!root) return;
  const params = new URLSearchParams(window.location.search);
  const search = document.getElementById('fh-ship-search');
  fhFilter.q = params.get('q') || '';
  search.value = fhFilter.q;
  FH_FACETS.forEach(facet => {
    const known = new Set(Array.from(root.querySelectorAll(`.fh-facet[data-facet="${facet}"] input`), b => b.dataset.value));
    params.getAll(facet).forEach(v => { if (known.has(v)) fhFilter[facet].add(v); });
  });

  search.addEventListener('input', () => { fhFilter.q = search.value; fhFilterApply(); });
  const facets = root.querySelectorAll('.fh-facet');
  facets.forEach(facet => {
    facet.querySelectorAll('input').forEach(box => {
      box.addEventListener('change', () => {
        const selected = fhFilter[facet.dataset.facet];
        if (box.checked) selected.add(box.dataset.value);
        else selected.delete(box.dataset.value);
        fhFilterApply();
      });
    });
    // One dropdown open at a time.
    facet.addEventListener('toggle', () => {
      if (!facet.open) return;
      facets.forEach(other => { if (other !== facet) other.open = false; });
      // Slide the menu left if it would run off the right edge (phones), keeping an 8px gutter.
      const menu = facet.querySelector('.fh-facet-menu');
      menu.style.left = '';
      const box = menu.getBoundingClientRect();
      const over = box.right - (document.documentElement.clientWidth - 8);
      if (over > 0) menu.style.left = `${-Math.max(0, Math.min(over, box.left - 8))}px`;
    });
  });
  document.addEventListener('click', e => {
    facets.forEach(facet => { if (facet.open && !facet.contains(e.target)) facet.open = false; });
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    // Only the dropdown being used. Safari doesn't focus a clicked summary, so
    // nothing focused counts too; Escape in another field belongs to that field.
    const active = document.activeElement;
    const idle = !active || active === document.body;
    facets.forEach(facet => {
      if (!facet.open || !(idle || facet.contains(active))) return;
      facet.open = false;
      facet.querySelector('summary').focus();
    });
  });
  document.querySelectorAll('[data-filter-clear]').forEach(btn => btn.addEventListener('click', () => {
    fhFilterClear();
    search.focus();
  }));
  fhFilterApply();
});

function toggleAllFleetCommand(btn) {
  const collapse = btn.textContent.trim() === 'Collapse All';
  document.querySelectorAll('[data-fc-collapse^="fc-doc-"], [data-fc-collapse^="fc-standalone-"]').forEach(header => {
    const body = document.getElementById(header.getAttribute('data-fc-collapse'));
    if (body) body.style.display = collapse ? 'none' : '';
    const arrow = header.querySelector('.doctrine-arrow');
    if (arrow) arrow.classList.toggle('open', !collapse);
  });
  btn.textContent = collapse ? 'Expand All' : 'Collapse All';
}

// ---------------------------------------------------------------------------
// Fleet Command readiness table — Main/Alt grouping + Name/Status/Corporation sort
// ---------------------------------------------------------------------------

function fcSortTable(table, sortKey, dir) {
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));

  // Bucket rows by owning user (Main + Alts); rows with no linked user stand alone.
  const byUser = new Map();
  const groupOrder = [];
  rows.forEach(row => {
    const uid = row.dataset.user;
    const key = uid || ('__solo_' + groupOrder.length);
    if (!byUser.has(key)) { byUser.set(key, []); groupOrder.push(key); }
    byUser.get(key).push(row);
  });

  const groups = groupOrder.map(key => {
    const groupRows = byUser.get(key);
    const main = groupRows.find(r => r.dataset.main === '1') || groupRows[0];
    const alts = groupRows.filter(r => r !== main).sort((a, b) => a.dataset.name.localeCompare(b.dataset.name));
    return { main, groupRows: [main, ...alts] };
  });

  const getVal = (row) => {
    if (sortKey === 'status') return parseInt(row.dataset.statusRank, 10);
    if (sortKey === 'corp') return row.dataset.corp.toLowerCase();
    return row.dataset.name.toLowerCase();
  };

  groups.sort((a, b) => {
    const va = getVal(a.main), vb = getVal(b.main);
    if (va < vb) return dir === 'asc' ? -1 : 1;
    if (va > vb) return dir === 'asc' ? 1 : -1;
    return 0;
  });

  const frag = document.createDocumentFragment();
  groups.forEach(g => g.groupRows.forEach(r => frag.appendChild(r)));
  tbody.appendChild(frag);
}

// Reopen the fit a filter toggle came from (the redirect carries #fit-<id>).
document.addEventListener('DOMContentLoaded', function() {
  const hash = window.location.hash;
  if (!hash.startsWith('#fit-')) return;
  const fit = document.getElementById(hash.slice(1));
  if (fit && fit.tagName === 'DETAILS') fit.open = true;
});

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.fc-readiness-table').forEach(function(table) {
    fcSortTable(table, 'name', 'asc');
    table.querySelectorAll('.fc-sort-hdr').forEach(function(hdr) {
      hdr.addEventListener('click', function() {
        const key = this.dataset.sort;
        const dir = this.dataset.dir === 'asc' ? 'desc' : 'asc';
        table.querySelectorAll('.fc-sort-hdr').forEach(h => delete h.dataset.dir);
        this.dataset.dir = dir;
        fcSortTable(table, key, dir);
      });
    });
  });
});

function toggleAllManage(btn) {
  const collapse = btn.textContent.trim() === 'Collapse All';
  document.querySelectorAll('[data-manage-collapse^="manage-doc-"]').forEach(header => {
    const body = document.getElementById(header.getAttribute('data-manage-collapse'));
    if (body) body.style.display = collapse ? 'none' : '';
    const arrow = header.querySelector('.doctrine-arrow');
    if (arrow) arrow.classList.toggle('open', !collapse);
  });
  btn.textContent = collapse ? 'Expand All' : 'Collapse All';
}


const ROMAN_TO_INT = { I: 1, II: 2, III: 3, IV: 4, V: 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5 };
const ROMAN = ["", "I", "II", "III", "IV", "V"];

// ---------------------------------------------------------------------------
// Skill / ship autocomplete
// ---------------------------------------------------------------------------

async function _fetchAutocomplete(url, query) {
  // Returns [] on network error or bad response rather than throwing.
  if (query.length < 2) return [];
  try {
    const res = await fetch(`${url}?q=${encodeURIComponent(query)}`);
    if (!res.ok) return [];
    return (await res.json()).results || [];
  } catch {
    return [];
  }
}

function _openDropdown(input, dropId, results, onSelect, clearInput = true) {
  // Builds dropdown items with DOM methods (not innerHTML) to avoid XSS.
  const drop = document.getElementById(dropId);
  if (!drop) return;
  drop.innerHTML = '';

  if (!results.length) {
    const empty = document.createElement('div');
    empty.className = 'skill-drop-empty';
    empty.textContent = 'No results';
    drop.appendChild(empty);
  } else {
    results.forEach(r => {
      const item = document.createElement('div');
      item.className = 'skill-drop-item';
      item.dataset.id = r.id;
      item.dataset.name = r.name;

      const nameSpan = document.createElement('span');
      nameSpan.className = 'skill-drop-name';
      nameSpan.textContent = r.name;

      const groupSpan = document.createElement('span');
      groupSpan.className = 'skill-drop-group';
      groupSpan.textContent = r['eve_group__name'] || '';

      item.appendChild(nameSpan);
      item.appendChild(groupSpan);
      item.addEventListener('mousedown', e => {
        e.preventDefault();
        onSelect(item.dataset.id, item.dataset.name);
        drop.classList.remove('open');
        if (clearInput) input.value = '';
      });
      drop.appendChild(item);
    });
  }
  drop.classList.add('open');
}

function closeDropdown(dropId) {
  const el = document.getElementById(dropId);
  if (el) el.classList.remove('open');
}

async function searchSkills(input, dropId, onSelect) {
  const results = await _fetchAutocomplete(window.FLEET_SKILL_AC_URL, input.value.trim());
  _openDropdown(input, dropId, results, onSelect);
}

async function searchShips(input, dropId, onSelect) {
  const results = await _fetchAutocomplete(window.FLEET_SHIP_AC_URL, input.value.trim());
  _openDropdown(input, dropId, results, onSelect, false);
}

// ---------------------------------------------------------------------------
// Skill tag builder (shared skills + tier skills)
// ---------------------------------------------------------------------------

function addSkillTag(container, typeId, name, level) {
  // Build tag with DOM methods to avoid innerHTML XSS risk on skill names.
  const tag = document.createElement('div');
  tag.className = 'skill-tag';
  tag.dataset.typeId = typeId;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'skill-tag-name';
  nameSpan.textContent = name;

  const sel = document.createElement('select');
  sel.className = 'skill-tag-level';
  sel.title = 'Required level';
  [['1','I'],['2','II'],['3','III'],['4','IV'],['5','V']].forEach(([val, label]) => {
    const opt = document.createElement('option');
    opt.value = val;
    opt.textContent = label;
    if (String(level) === val) opt.selected = true;
    sel.appendChild(opt);
  });

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'skill-tag-remove';
  btn.title = 'Remove';
  btn.textContent = '×';
  btn.addEventListener('click', () => tag.remove());

  tag.appendChild(nameSpan);
  tag.appendChild(sel);
  tag.appendChild(btn);
  container.appendChild(tag);
}

function getSkillTagsData(container) {
  return Array.from(container.querySelectorAll('.skill-tag')).map(tag => ({
    type_id: parseInt(tag.dataset.typeId),
    level: parseInt(tag.querySelector('.skill-tag-level').value),
  }));
}

// ---------------------------------------------------------------------------
// Tier builder
// ---------------------------------------------------------------------------

let tierCount = 0;
const MAX_TIERS = 10; // _save_fit only persists tier_label_1..10 — extra tiers are silently dropped
const usedTierNumbers = new Set(); // tracks in-use tier_*_N suffixes so removed slots can be reused

function addTierBlock(label, desc, warn) {
  if (usedTierNumbers.size >= MAX_TIERS) {
    alert(`A fit can have at most ${MAX_TIERS} tiers.`);
    return;
  }
  let n = 1;
  while (usedTierNumbers.has(n)) n++;
  usedTierNumbers.add(n);
  tierCount = n; // kept for legacy callers; prefer the return value
  const container = document.getElementById('tiers-builder');
  if (!container) return;
  const block = document.createElement('div');
  block.className = 'tier-builder';
  block.dataset.tier = n;
  // Build with empty values and assign label/desc/warn via DOM properties below —
  // interpolating them into this HTML string would let a quote in saved tier text
  // (e.g. a label like `x" onmouseover="…`) break out of the attribute (stored XSS).
  block.innerHTML = `
    <div class="tier-builder-hdr">
      <input type="text" name="tier_label_${n}" class="form-control form-control-sm tier-label-input"
             placeholder="Tier label (e.g. Minimum) — required" style="max-width:220px">
      <input type="text" name="tier_desc_${n}" class="form-control form-control-sm tier-desc-input"
             placeholder="Description (optional)" style="flex:1">
      <button type="button" class="fh-btn fh-btn-danger" onclick="removeTierBlock(this.closest('.tier-builder'))">Remove</button>
    </div>
    <div class="fh-form-group" style="margin-bottom:8px">
      <input type="text" name="tier_warn_${n}" class="form-control form-control-sm tier-warn-input"
             placeholder="Warning text (optional — shown as yellow banner)">
    </div>
    <div class="skill-search-wrap" style="margin-bottom:6px">
      <input type="text" class="form-control form-control-sm tier-skill-search"
             placeholder="Search skill to add…" autocomplete="off"
             oninput="searchSkills(this,'tier-drop-${n}', (id,name)=>{ addSkillTag(document.getElementById('tier-tags-${n}'),id,name,4); })"
             onfocus="searchSkills(this,'tier-drop-${n}', (id,name)=>{ addSkillTag(document.getElementById('tier-tags-${n}'),id,name,4); })"
             onblur="setTimeout(()=>closeDropdown('tier-drop-${n}'),150)">
      <div id="tier-drop-${n}" class="skill-dropdown"></div>
    </div>
    <div id="tier-tags-${n}" class="skill-tags-wrap"></div>
    <input type="hidden" name="tier_skills_${n}" id="tier-skills-hidden-${n}">
    <details style="margin-top:8px" open>
      <summary style="font-size:11px;color:var(--bs-secondary-color);cursor:pointer">Paste skill list</summary>
      <textarea class="form-control form-control-sm mt-2" rows="3"
                placeholder="One skill per line: Shield Upgrades V or Shield Upgrades 5"></textarea>
      <button type="button" class="fh-btn fh-btn-secondary mt-1"
              onclick="importPasteList(this, 'tier-tags-${n}')">Import</button>
    </details>`;
  block.querySelector('.tier-label-input').value = label || '';
  block.querySelector('.tier-desc-input').value = desc || '';
  block.querySelector('.tier-warn-input').value = warn || '';
  container.appendChild(block);
  return n;
}

function removeTierBlock(block) {
  // Free up this slot's tier_*_N suffix so a later "+ Add Tier" can reuse it —
  // otherwise repeated add/remove would exhaust MAX_TIERS without 10 tiers
  // ever being on the form at once.
  usedTierNumbers.delete(parseInt(block.dataset.tier, 10));
  block.remove();
}

async function importPasteList(btn, tagsContainerId) {
  const textarea = btn.previousElementSibling;
  const container = document.getElementById(tagsContainerId);
  const { recognised, unrecognised } = parsePasteList(textarea.value);

  if (!recognised.length && !unrecognised.length) return;

  // Disable Save and show loading state while lookups run
  const saveBtn = btn.closest('form')?.querySelector('[type=submit]');
  const origBtnText = btn.textContent;
  btn.textContent = 'Importing…';
  btn.disabled = true;
  if (saveBtn) saveBtn.disabled = true;

  // Deduplicate — keep highest level per skill name
  const deduped = {};
  for (const r of recognised) {
    const key = r.name.toLowerCase();
    if (!deduped[key] || r.level > deduped[key].level) deduped[key] = r;
  }

  // Get names already in container to avoid duplicates
  const existing = new Set(
    Array.from(container.querySelectorAll('.skill-tag-name'))
      .map(el => el.textContent.trim().toLowerCase())
  );

  const failed = [];
  try {
    for (const r of Object.values(deduped)) {
      if (existing.has(r.name.toLowerCase())) continue;
      try {
        const res = await fetch(`${window.FLEET_SKILL_EXACT_URL}?name=${encodeURIComponent(r.name)}`);
        const data = await res.json();
        if (data.id) {
          addSkillTag(container, data.id, data.name, r.level);
          existing.add(data.name.toLowerCase());
        } else {
          failed.push(r.name);
        }
      } catch {
        failed.push(r.name);
      }
    }
  } finally {
    // Always re-enable regardless of errors so Save is never stuck disabled
    textarea.value = '';
    btn.textContent = origBtnText;
    btn.disabled = false;
    if (saveBtn) saveBtn.disabled = false;
  }

  const allFailed = [...failed, ...unrecognised];
  if (allFailed.length) alert(`Could not find — check spelling:\n${allFailed.join('\n')}`);
}

function serializeTiers() {
  try {
    document.querySelectorAll('.tier-builder').forEach(block => {
      const n = block.dataset.tier;
      const hidden = document.getElementById(`tier-skills-hidden-${n}`);
      const tagsEl = document.getElementById(`tier-tags-${n}`);
      if (hidden && tagsEl) {
        hidden.value = JSON.stringify(getSkillTagsData(tagsEl));
      }
    });
    serializePiPlans();
  } catch(e) {
    console.error('Fleet Hub: failed to serialize form data', e);
  }
}

function serializePiPlans() {
  const piHidden = document.getElementById('pi-plans-hidden');
  if (!piHidden) return;
  const plans = Array.from(document.querySelectorAll('.pi-plan-block')).map(planEl => ({
    name: planEl.querySelector('.pi-plan-name')?.value.trim() || '',
    desc: planEl.querySelector('.pi-plan-desc')?.value.trim() || '',
    planets: Array.from(planEl.querySelectorAll('.pi-planet-block')).map(pEl => ({
      planet:   pEl.querySelector('.pi-planet-type')?.value || '',
      name:     pEl.querySelector('.pi-planet-name')?.value.trim() || '',
      produces: pEl.querySelector('.pi-planet-produces')?.value.trim() || '',
      desc:     pEl.querySelector('.pi-planet-desc')?.value.trim() || '',
      template: pEl.querySelector('.pi-planet-template')?.value.trim() || '',
    })),
  })).filter(p => p.name);
  piHidden.value = JSON.stringify(plans.length ? plans : null);
}

// ---------------------------------------------------------------------------
// Paste list import
// Accepts: "Skill Name V" or "Skill Name 5" — one per line
// Returns: [{name, level}] parsed entries; unrecognised lines returned separately
// ---------------------------------------------------------------------------

function parsePasteList(text) {
  const recognised = [];
  const unrecognised = [];

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    const parts = line.split(" ");
    const levelToken = parts[parts.length - 1];
    const level = ROMAN_TO_INT[levelToken];

    if (level) {
      recognised.push({ name: parts.slice(0, -1).join(" "), level });
    } else {
      unrecognised.push(line);
    }
  }

  return { recognised, unrecognised };
}
