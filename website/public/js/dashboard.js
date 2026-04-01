// ─── State ────────────────────────────────────────────
let guildsData  = [];
let guildData   = null;
let currentGuildId = null;
let dirty       = false;
let overviewChart = null;
let miniCharts = [];

function redirectToDashboardLogin() {
    const next = encodeURIComponent(location.pathname + location.search);
    location.href = '/dashboard/login?next=' + next;
}

async function fetchJsonOrAuth(url, options = undefined) {
    const res = await fetch(url, options);
    if (res.status === 401) {
        redirectToDashboardLogin();
        throw new Error('Unauthorized');
    }
    return res;
}

// ─── Boot ──────────────────────────────────────────────
async function init() {
    try {
        const res = await fetchJsonOrAuth('/api/dashboard/guilds');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        guildsData = await res.json();
        renderGuildPicker(guildsData);

        const statsRes = await fetch('/api/stats');
        const stats    = await statsRes.json();
        if (stats.avatar) {
            document.getElementById('favicon').href         = stats.avatar;
            document.getElementById('sidebarBotAvatar').src = stats.avatar;
        }
        if (stats.username) {
            document.getElementById('sidebarBotName').textContent = stats.username;
        }

        const match = location.pathname.match(/^\/dashboard\/(\d+)/);
        if (match) selectGuild(match[1]);
    } catch (e) {
        console.error('Dashboard init failed:', e);
        document.getElementById('guildPickGrid').innerHTML =
            '<p class="pick-loading">Failed to load servers. Is the bot online?</p>';
    }
}

// ─── Guild Picker ──────────────────────────────────────
function renderGuildPicker(guilds) {
    const grid = document.getElementById('guildPickGrid');
    if (!guilds.length) {
        grid.innerHTML = '<p class="pick-loading">No manageable servers found. You need Administrator permission in a server where the bot is present.</p>';
        return;
    }
    grid.innerHTML = guilds.map(g => `
        <div class="guild-pick-card" onclick="selectGuild('${g.id}')">
            <img src="${g.icon || '/avatar.png'}" alt="" onerror="this.src='/avatar.png'">
            <span class="guild-pick-name">${esc(g.name)}</span>
            <span class="guild-pick-count">${g.member_count.toLocaleString()} members</span>
        </div>
    `).join('');
}

// ─── Select Guild ──────────────────────────────────────
async function selectGuild(id) {
    currentGuildId = id;
    document.getElementById('guildPickScreen').style.display = 'none';
    document.getElementById('appShell').style.display        = 'flex';

    try {
        const res = await fetchJsonOrAuth('/api/dashboard/' + id);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        guildData = await res.json();
    } catch (e) {
        showToast('Failed to load guild data', true);
        return;
    }

    document.getElementById('sidebarServerName').textContent = guildData.name;
    document.getElementById('sidebarServerIcon').src         = guildData.icon || '/avatar.png';

    document.getElementById('guildDropdownList').innerHTML = guildsData.map(g => `
        <div class="guild-option" onclick="selectGuild('${g.id}')">
            <img src="${g.icon || '/avatar.png'}" onerror="this.src='/avatar.png'">
            <span>${esc(g.name)}</span>
        </div>
    `).join('');

    history.pushState({}, '', '/dashboard/' + id);
    populateAll();
    navigate('overview');
}

// ─── Navigation ────────────────────────────────────────
const PAGE_LABELS = {
    overview:        ['Server',        'Overview'],
    settings:        ['Server',        'Settings'],
    leaderboard:     ['Server',        'Leaderboard'],
    antinuke:        ['Security',      'Antinuke'],
    joingate:        ['Security',      'Join Gate'],
    fakepermissions: ['Security',      'Fake Permissions'],
    roles:           ['Configuration', 'Roles'],
    messages:        ['Configuration', 'Messages'],
    starboard:       ['Configuration', 'Starboard'],
    voicemaster:     ['Configuration', 'VoiceMaster'],
    levelrewards:    ['Configuration', 'Level Rewards'],
    bumpreward:      ['Configuration', 'Bump Reminder'],
    reactiontriggers:['Configuration', 'Reaction Triggers'],
    aliases:         ['Configuration', 'Command Aliases'],
    automod:         ['Configuration', 'Automod'],
    logging:         ['Configuration', 'Logging'],
};

function navigate(pageId) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const page  = document.getElementById('page-' + pageId);
    const navEl = document.querySelector('.nav-item[data-page="' + pageId + '"]');
    if (page)  page.classList.add('active');
    if (navEl) navEl.classList.add('active');
    const [section, title] = PAGE_LABELS[pageId] || ['', pageId];
    document.getElementById('breadcrumbSection').textContent = section;
    document.getElementById('breadcrumbPage').textContent    = title;
}

// ─── Populate ──────────────────────────────────────────
function populateAll() {
    if (!guildData) return;
    const c  = guildData.config  || {};
    const sb = guildData.starboard || {};

    document.getElementById('ovGuildName').textContent  = guildData.name;
    document.getElementById('ovMemberVal').textContent  = fmt(guildData.member_count);
    document.getElementById('ovChannelVal').textContent = fmt(guildData.channel_count);
    document.getElementById('ovRoleVal').textContent    = fmt(guildData.role_count);
    document.getElementById('ovMemberBig').textContent  = fmt(guildData.member_count);

    const delta = document.getElementById('ovMemberDelta');
    if (delta) {
        delta.textContent = `${fmt(guildData.member_count)} total members`;
        delta.className   = 'ov-chart-delta';
    }

    let modules = 0;
    if (c.antinuke_enabled)    modules++;
    if (c.voicemaster_enabled) modules++;
    if (c.leveling_enabled)    modules++;
    if (c.filter_invites)      modules++;
    if (c.filter_spam)         modules++;
    if (c.filter_words)        modules++;
    document.getElementById('ovModuleVal').textContent = modules;

    populateChannelSelects(guildData.channels || []);
    populateVoiceSelects(guildData.channels   || []);
    populateRoleSelects(guildData.roles       || []);

    setVal('cfg-prefix', c.prefix);
    setVal('cfg-mute-role', c.mute_role_id);
    setVal('cfg-jail-role', c.jail_role_id);

    setCheck('cfg-antinuke-enabled',  c.antinuke_enabled);
    setVal('cfg-antinuke-action',     c.antinuke_action);
    setVal('cfg-antinuke-threshold',  c.antinuke_threshold);

    setVal('cfg-modlog-channel', c.modlog_channel_id);

    setCheck('cfg-filter-invites',       c.filter_invites);
    setVal('cfg-filter-invites-action',  c.filter_invites_action);
    setCheck('cfg-filter-spam',          c.filter_spam);
    setVal('cfg-filter-spam-action',     c.filter_spam_action);
    setCheck('cfg-filter-words',         c.filter_words);
    setVal('cfg-filter-words-action',    c.filter_words_action);

    setCheck('cfg-vm-enabled',  c.voicemaster_enabled);
    setVal('cfg-vm-channel',    c.voicemaster_channel_id);

    setCheck('cfg-lvl-enabled', c.leveling_enabled);
    setVal('cfg-lvl-channel',   c.level_channel_id);
    setVal('cfg-lvl-message',   c.level_message);

    setVal('cfg-sb-channel',   sb.starboard_channel_id || null);
    setVal('cfg-sb-threshold', sb.threshold || 5);
    setVal('cfg-sb-emoji',     sb.emoji     || '⭐');

    drawOverviewChart();
    drawMiniCharts();
    populateOverviewTables();
}

function setVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = (val !== null && val !== undefined) ? val : '';
}

function setCheck(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = !!val;
}

function fmt(n) { return (n || 0).toLocaleString(); }

// ─── Channel / Role options ─────────────────────────────
function makeOpts(items, vFn, lFn) {
    return '<option value="">— None —</option>' +
        items.map(i => '<option value="' + vFn(i) + '">' + esc(lFn(i)) + '</option>').join('');
}

function populateChannelSelects(channels) {
    const text = channels.filter(c => c.type === 0 || c.type === 5);
    const opts = makeOpts(text, c => c.id, c => '#' + c.name);
    ['cfg-modlog-channel', 'cfg-lvl-channel', 'cfg-sb-channel', 'cfg-joingate-channel']
        .forEach(id => assignOpts(id, opts));
}

function populateVoiceSelects(channels) {
    const voice = channels.filter(c => c.type === 2);
    const opts  = makeOpts(voice, c => c.id, c => '🔊 ' + c.name);
    assignOpts('cfg-vm-channel', opts);
}

function populateRoleSelects(roles) {
    const filtered = roles.filter(r => r.name !== '@everyone');
    const opts     = makeOpts(filtered, r => r.id, r => '@' + r.name);
    ['cfg-mute-role', 'cfg-jail-role'].forEach(id => assignOpts(id, opts));
}

function assignOpts(id, opts) {
    const el = document.getElementById(id);
    if (!el) return;
    const cur = el.value;
    el.innerHTML = opts;
    if (cur) el.value = cur;
}

// ─── Overview Tables ──────────────────────────────────
function populateOverviewTables() {
    if (!guildData) return;

    const textChannels = (guildData.channels || [])
        .filter(c => c.type === 0 || c.type === 5)
        .slice(0, 10);

    const ctb = document.getElementById('ovChannelTable');
    if (ctb) {
        ctb.innerHTML = textChannels.length
            ? textChannels.map(c => `
                <tr>
                    <td><span class="ch-badge"><span class="ch-hash">#</span>${esc(c.name)}</span></td>
                    <td style="color:var(--text-dim);font-size:12px">${c.type === 5 ? 'Announcement' : 'Text'}</td>
                    <td><code>${c.id}</code></td>
                </tr>`).join('')
            : '<tr><td colspan="3" style="color:var(--text-dim);text-align:center;padding:20px">No text channels found.</td></tr>';
    }

    const roles = (guildData.roles || []).slice(0, 10);
    const rtb = document.getElementById('ovRoleTable');
    if (rtb) {
        rtb.innerHTML = roles.length
            ? roles.map(r => {
                const hex = r.color ? '#' + r.color.toString(16).padStart(6, '0') : '#4a4f5c';
                return `
                <tr>
                    <td><span style="display:inline-flex;align-items:center;gap:8px">
                        <span class="role-dot" style="background:${hex}"></span>${esc(r.name)}
                    </span></td>
                    <td><code>${hex}</code></td>
                    <td><code>${r.id}</code></td>
                </tr>`;
            }).join('')
            : '<tr><td colspan="3" style="color:var(--text-dim);text-align:center;padding:20px">No roles found.</td></tr>';
    }
}

// ─── Time Pills ───────────────────────────────────────
function setTimePill(el, _period) {
    document.querySelectorAll('.time-pill').forEach(p => p.classList.remove('active'));
    el.classList.add('active');
    // Re-draw chart with same data (real period data would require backend tracking)
    drawOverviewChart();
}

// ─── Charts ────────────────────────────────────────────
function drawOverviewChart() {
    const ctx = document.getElementById('overviewChart');
    if (!ctx || !guildData) return;
    if (overviewChart) { overviewChart.destroy(); overviewChart = null; }

    // Pin canvas to exact container dimensions before Chart.js measures it
    const wrap = ctx.parentElement;
    const h = wrap ? wrap.offsetHeight || 190 : 190;
    ctx.style.width  = '100%';
    ctx.style.height = h + 'px';

    const base = guildData.member_count || 100;
    const pts  = Array.from({length: 7}, (_, i) =>
        Math.max(1, base - (6 - i) * Math.max(1, Math.floor(base * 0.008)))
    );
    pts[6] = base;
    overviewChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],
            datasets: [{
                data: pts,
                borderColor: 'rgba(165,180,252,0.85)',
                borderWidth: 2,
                tension: 0.4,
                pointRadius: 0,
                fill: true,
                backgroundColor: ctx2 => {
                    const g = ctx2.chart.ctx.createLinearGradient(0, 0, 0, 170);
                    g.addColorStop(0, 'rgba(165,180,252,0.14)');
                    g.addColorStop(1, 'transparent');
                    return g;
                }
            }]
        },
        options: {
            responsive: false,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false },
                tooltip: { callbacks: { label: c => ' ' + c.parsed.y.toLocaleString() + ' members' } }
            },
            scales: {
                y: { display: false },
                x: { grid: { display: false }, ticks: { color: '#4b5563', font: { size: 10 } } }
            }
        }
    });
}

function drawMiniCharts() {
    miniCharts.forEach(ch => {
        try { ch.destroy(); } catch (_) {}
    });
    miniCharts = [];

    const configs = [
        { id: 'miniChart1', base: guildData.member_count  || 50 },
        { id: 'miniChart2', base: guildData.channel_count || 10 },
        { id: 'miniChart3', base: guildData.role_count    || 5  },
        { id: 'miniChart4', base: 6 },
    ];
    configs.forEach(({ id, base }) => {
        const ctx = document.getElementById(id);
        if (!ctx) return;
        const pts = Array.from({length: 7}, (_, i) =>
            Math.max(1, base - (6 - i) * Math.max(1, Math.floor(base * 0.02)))
        );
        pts[6] = base;
        // Pin canvas dimensions before Chart.js touches them
        ctx.style.width  = '100%';
        ctx.style.height = '46px';
        const chart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: Array(7).fill(''),
                datasets: [{ data: pts, borderColor: 'rgba(165,180,252,0.55)', borderWidth: 1.5, tension: 0.4, pointRadius: 0, fill: false }]
            },
            options: {
                responsive: false,
                maintainAspectRatio: false,
                animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: { y: { display: false }, x: { display: false } }
            }
        });
        miniCharts.push(chart);
    });
}

// ─── Save ──────────────────────────────────────────────
function markDirty() {
    dirty = true;
    document.getElementById('saveBar').classList.add('visible');
}

function discardChanges() {
    dirty = false;
    document.getElementById('saveBar').classList.remove('visible');
    populateAll();
}

async function saveSettings() {
    if (!currentGuildId) return;
    const btn = document.querySelector('.btn-save');
    btn.textContent = 'Saving…';
    btn.disabled    = true;

    const get = id => document.getElementById(id);

    const payload = {
        prefix:                get('cfg-prefix')?.value            || ';',
        antinuke_enabled:      get('cfg-antinuke-enabled')?.checked  || false,
        antinuke_action:       get('cfg-antinuke-action')?.value      || 'ban',
        antinuke_threshold:    parseInt(get('cfg-antinuke-threshold')?.value) || 3,
        modlog_channel_id:     get('cfg-modlog-channel')?.value      || null,
        filter_invites:        get('cfg-filter-invites')?.checked    || false,
        filter_invites_action: get('cfg-filter-invites-action')?.value || 'kick',
        filter_spam:           get('cfg-filter-spam')?.checked       || false,
        filter_spam_action:    get('cfg-filter-spam-action')?.value  || 'kick',
        filter_words:          get('cfg-filter-words')?.checked      || false,
        filter_words_action:   get('cfg-filter-words-action')?.value || 'kick',
        voicemaster_enabled:   get('cfg-vm-enabled')?.checked        || false,
        voicemaster_channel_id:get('cfg-vm-channel')?.value          || null,
        leveling_enabled:      get('cfg-lvl-enabled')?.checked       || false,
        level_channel_id:      get('cfg-lvl-channel')?.value         || null,
        level_message:         get('cfg-lvl-message')?.value         || null,
        mute_role_id:          get('cfg-mute-role')?.value            || null,
        jail_role_id:          get('cfg-jail-role')?.value            || null,
        starboard_channel_id:  get('cfg-sb-channel')?.value          || null,
        starboard_threshold:   parseInt(get('cfg-sb-threshold')?.value) || 5,
        starboard_emoji:       get('cfg-sb-emoji')?.value             || '⭐',
    };

    try {
        const res = await fetchJsonOrAuth('/api/dashboard/' + currentGuildId + '/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (res.ok) {
            dirty = false;
            document.getElementById('saveBar').classList.remove('visible');
            showToast('Settings saved successfully');
        } else {
            showToast('Failed to save settings', true);
        }
    } catch (_) {
        showToast('Network error — please try again', true);
    } finally {
        btn.textContent = 'Save Changes';
        btn.disabled    = false;
    }
}

// ─── Guild Dropdown ────────────────────────────────────
function toggleGuildDropdown(e) {
    e.stopPropagation();
    document.getElementById('guildDropdown').classList.toggle('open');
}

document.addEventListener('click', () => {
    document.getElementById('guildDropdown')?.classList.remove('open');
});

// ─── Toast ────────────────────────────────────────────
function showToast(msg, isError = false) {
    const t = document.createElement('div');
    t.className   = 'toast' + (isError ? ' error' : '');
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2800);
}

// ─── Util ─────────────────────────────────────────────
function esc(str) {
    const d = document.createElement('div');
    d.textContent = str ?? '';
    return d.innerHTML;
}

init();
