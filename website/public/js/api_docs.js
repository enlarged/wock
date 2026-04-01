(function () {
    const sections = document.getElementById('endpointSections');
    const globalApiKey = document.getElementById('globalApiKey');

    function esc(text) {
        return String(text ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function byCategory(endpoints) {
        const map = {};
        for (const ep of endpoints || []) {
            const cat = ep.category || 'Other';
            if (!map[cat]) map[cat] = [];
            map[cat].push(ep);
        }
        return Object.entries(map).sort((a, b) => a[0].localeCompare(b[0]));
    }

    function endpointCard(ep, idx) {
        const params = Array.isArray(ep.params) ? ep.params : [];
        const bodyId = `endpointBody${idx}`;
        const resId = `endpointRes${idx}`;

        const paramInputs = params.length
            ? `<div class="param-grid">${params
                  .map(
                      (p) => `
                <div class="param-wrap">
                    <label>${esc(p)}</label>
                    <input class="param-input" data-param="${esc(p)}" placeholder="${esc(p)}" />
                </div>
            `
                  )
                  .join('')}</div>`
            : '';

        return `
            <article class="endpoint-card" data-index="${idx}">
                <button class="endpoint-head" type="button" data-toggle="${bodyId}">
                    <span class="method ${esc(ep.method || 'GET')}">${esc(ep.method || 'GET')}</span>
                    <span class="path">${esc(ep.path || '')}</span>
                    <span class="lock">${ep.requiresAuth ? '<i class="fas fa-lock" title="Requires API key"></i>' : '<i class="fas fa-lock-open" title="Public endpoint"></i>'}</span>
                    <span class="desc">${esc(ep.desc || '')}</span>
                </button>
                <div class="endpoint-body" id="${bodyId}">
                    ${paramInputs}
                    <div class="actions">
                        <button class="test-btn" type="button" data-run="${idx}">Test endpoint</button>
                        <span class="meta" id="${resId}Meta"></span>
                    </div>
                    <pre class="response" id="${resId}">// response will appear here</pre>
                </div>
            </article>
        `;
    }

    function render(endpoints) {
        const grouped = byCategory(endpoints);
        sections.innerHTML = grouped
            .map(
                ([category, eps]) => `
            <section>
                <h2 class="category-title">${esc(category)}</h2>
                ${eps.map((ep, idx) => endpointCard(ep, `${category}-${idx}`)).join('')}
            </section>
        `
            )
            .join('');
    }

    async function runEndpoint(card, endpoint) {
        const responseEl = card.querySelector('.response');
        const metaEl = card.querySelector('.meta');

        responseEl.textContent = '// processing request...';
        metaEl.textContent = '';

        const headers = {};
        if (endpoint.requiresAuth) {
            const key = (globalApiKey.value || '').trim();
            if (!key) {
                responseEl.textContent = JSON.stringify({ error: 'API key is required for this endpoint.' }, null, 2);
                return;
            }
            headers['x-api-key'] = key;
        }

        const query = new URLSearchParams();
        card.querySelectorAll('[data-param]').forEach((input) => {
            const name = input.getAttribute('data-param');
            const value = input.value.trim();
            if (name && value) query.append(name, value);
        });

        let path = endpoint.path || '/';
        const queryString = query.toString();
        if (queryString) path += `?${queryString}`;

        try {
            const start = performance.now();
            const res = await fetch(path, { method: endpoint.method || 'GET', headers });

            let body;
            const text = await res.text();
            try {
                body = JSON.parse(text);
            } catch {
                body = text;
            }

            const end = performance.now();
            responseEl.textContent = typeof body === 'string' ? body : JSON.stringify(body, null, 2);
            metaEl.innerHTML = `status <span style="color:${res.ok ? 'var(--ok)' : 'var(--bad)'}">${res.status}</span> · ${Math.round(end - start)}ms`;
        } catch (err) {
            responseEl.textContent = JSON.stringify({ error: err.message }, null, 2);
        }
    }

    async function init() {
        try {
            const res = await fetch('/api/endpoints', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const payload = await res.json();
            const endpoints = Array.isArray(payload.endpoints) ? payload.endpoints : [];
            render(endpoints);

            sections.addEventListener('click', async (e) => {
                const toggleId = e.target.closest('[data-toggle]')?.getAttribute('data-toggle');
                if (toggleId) {
                    const body = document.getElementById(toggleId);
                    if (body) body.classList.toggle('open');
                    return;
                }

                const runBtn = e.target.closest('[data-run]');
                if (!runBtn) return;

                const key = runBtn.getAttribute('data-run');
                const card = runBtn.closest('.endpoint-card');
                if (!card) return;

                const endpointPath = card.querySelector('.path')?.textContent || '';
                const endpoint = endpoints.find((ep) => ep.path === endpointPath) || null;
                if (!endpoint) return;

                await runEndpoint(card, endpoint);
            });
        } catch (err) {
            sections.innerHTML = `<div class="auth-panel">Failed to load endpoint index: ${esc(err.message)}</div>`;
        }
    }

    document.addEventListener('DOMContentLoaded', init);
})();
