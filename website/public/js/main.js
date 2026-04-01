// ---- Keyboard Navigation ----
const items = Array.from(document.querySelectorAll('.menu-item'));
let cursor = -1;

function setActive(idx) {
    items.forEach(el => el.classList.remove('active'));
    if (idx >= 0 && idx < items.length) {
        items[idx].classList.add('active');
        cursor = idx;
    }
}

document.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive(Math.min(cursor + 1, items.length - 1));
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(Math.max(cursor - 1, 0));
    } else if (e.key === 'Enter' && cursor >= 0) {
        items[cursor].click();
    }
});

items.forEach((el, i) => {
    el.addEventListener('mouseenter', () => setActive(i));
    el.addEventListener('mouseleave', () => {});
});

// ---- Signal bar ping indicator ----
function animateSignal(ping) {
    const bars = document.querySelectorAll('.sig-bar');
    const lit = ping < 80 ? 4 : ping < 150 ? 3 : ping < 300 ? 2 : 1;
    bars.forEach((b, i) => b.classList.toggle('lit', i < lit));
}

// ---- Activity bar animation ----
const barEls = Array.from(document.querySelectorAll('#nodeBars .bar'));
const history = barEls.map(() => Math.random() * 0.4 + 0.15);

function tickBars(guilds) {
    const norm = guilds > 0 ? Math.min(guilds / 600, 1) : (Math.random() * 0.3 + 0.1);
    history.shift();
    history.push(Math.max(0.05, norm * 0.85 + 0.05 + (Math.random() * 0.12)));
    const max = Math.max(...history);
    barEls.forEach((b, i) => {
        const pct = Math.round((history[i] / max) * 100);
        b.style.height = pct + '%';
        b.classList.toggle('active', i >= barEls.length - 3);
    });
}

// ---- API Stats ----
async function syncStats() {
    try {
        const res = await fetch('/api/stats');
        if (!res.ok) return;
        const d = await res.json();

        if (d.avatar) {
            document.getElementById('favicon').href = d.avatar;
            document.getElementById('nodeAvatar').src = d.avatar;
        }
        if (d.name) document.getElementById('nodeName').textContent = d.name;
        const ping = d.ping != null ? d.ping : null;
        document.getElementById('nodePing').textContent = ping != null ? ping + 'ms' : '—';
        document.getElementById('nodeGuilds').textContent = d.guilds != null ? Number(d.guilds).toLocaleString() : '—';
        document.getElementById('nodeUsers').textContent = d.users != null ? Number(d.users).toLocaleString() : '—';

        tickBars(d.guilds || 0);
        if (ping != null) animateSignal(ping);
    } catch (_) {}
}

syncStats();
setInterval(syncStats, 15000);
setInterval(() => tickBars(0), 2800);
