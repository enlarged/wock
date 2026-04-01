(function () {
    const ICON_MAP = {
        configuration: 'fa-sliders-h',
        moderation: 'fa-shield-alt',
        utility: 'fa-tools',
        information: 'fa-circle-info',
        economy: 'fa-coins',
        entertainment: 'fa-gamepad',
        miscellaneous: 'fa-wand-magic-sparkles',
        lastfm: 'fa-music',
        socials: 'fa-users',
        pokemon: 'fa-dragon',
    };

    const state = {
        byModule: {},
        modules: [],
        activeModule: null,
        query: '',
    };

    const dockItems = document.getElementById('dockItems');
    const grid = document.getElementById('commandsGrid');
    const titleEl = document.getElementById('categoryName');
    const countEl = document.getElementById('commandCount');
    const searchInput = document.getElementById('commandSearch');
    const currentIcon = document.getElementById('currentIcon');
    const moduleToggle = document.getElementById('moduleToggle');
    const moduleToggleLabel = document.getElementById('moduleToggleLabel');
    const moduleMenu = document.getElementById('moduleMenu');

    function esc(text) {
        return String(text ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeModuleName(moduleName) {
        if (!moduleName) return 'Uncategorized';
        return moduleName.charAt(0).toUpperCase() + moduleName.slice(1);
    }

    function moduleIcon(moduleName) {
        const key = String(moduleName || '').toLowerCase();
        return ICON_MAP[key] || 'fa-terminal';
    }

    function countCommandsRecursive(cmds) {
        let count = 0;
        for (const cmd of cmds || []) {
            count += 1;
            if (Array.isArray(cmd.subcommands) && cmd.subcommands.length) {
                count += countCommandsRecursive(cmd.subcommands);
            }
        }
        return count;
    }

    function matchesRecursive(cmd, query) {
        if (!query) return true;

        const haystack = [
            cmd.name,
            cmd.description,
            cmd.syntax,
            ...(cmd.aliases || []),
        ]
            .join(' ')
            .toLowerCase();

        if (haystack.includes(query)) return true;

        return (cmd.subcommands || []).some((sub) => matchesRecursive(sub, query));
    }

    function filterRecursive(cmds, query) {
        if (!query) return cmds;

        const out = [];
        for (const cmd of cmds || []) {
            const subs = filterRecursive(cmd.subcommands || [], query);
            if (matchesRecursive(cmd, query)) {
                out.push({ ...cmd, subcommands: subs });
            }
        }
        return out;
    }

    function flattenCommands(cmds, parent = '') {
        const flat = [];
        for (const cmd of cmds || []) {
            const current = {
                ...cmd,
                _isSub: Boolean(parent),
                _parent: parent || '',
            };
            flat.push(current);
            if (Array.isArray(cmd.subcommands) && cmd.subcommands.length) {
                flat.push(...flattenCommands(cmd.subcommands, cmd.name || parent));
            }
        }
        return flat;
    }

    function renderCommandCard(cmd) {
        const aliases = Array.isArray(cmd.aliases) ? cmd.aliases : [];
        const syntax = cmd.syntax || 'none';
        const perms = cmd.permissions || 'none';

        const aliasChips = aliases.length
            ? `<div class="chip-row">${aliases.map((a) => `<span class="chip">${esc(a)}</span>`).join('')}</div>`
            : `<div class="chip-row"><span class="chip">none</span></div>`;

        const subRef = cmd._isSub
            ? `<p class="sub-ref"><i class="fas fa-code-branch"></i> Subcommand of ${esc(cmd._parent)}</p>`
            : '';

        return `
            <div class="cmd-head">
                <h3 class="cmd-name">${esc(cmd.name || 'unknown')}</h3>
                <button class="copy-indicator fa-regular fa-copy" type="button" title="Copy syntax" data-copy="${esc(syntax)}"></button>
            </div>
            <p class="cmd-desc">${esc(cmd.description || 'No description provided.')}</p>
            ${subRef}

            <div class="card-scroll">
                <div class="meta-label">Syntax</div>
                <div class="chip-row"><span class="chip">${esc(syntax)}</span></div>

                <div class="meta-label">Permissions</div>
                <div class="chip-row"><span class="chip">${esc(perms)}</span></div>

                <div class="meta-label">Aliases</div>
                ${aliasChips}
            </div>
        `;
    }

    function setMenuOpen(open) {
        moduleToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        moduleMenu.classList.toggle('open', open);
    }

    function renderDock() {
        dockItems.innerHTML = state.modules
            .map((mod) => {
                const active = mod === state.activeModule ? 'active' : '';
                return `
                    <button class="dock-btn ${active}" type="button" data-module="${esc(mod)}" title="${esc(mod)}">
                        <i class="fas ${moduleIcon(mod)}"></i>
                    </button>
                `;
            })
            .join('');

        dockItems.querySelectorAll('.dock-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                state.activeModule = btn.dataset.module;
                setMenuOpen(false);
                render();
            });
        });
    }

    function renderModuleMenu() {
        moduleMenu.innerHTML = state.modules
            .map((mod) => {
                const count = countCommandsRecursive(state.byModule[mod] || []);
                const active = mod === state.activeModule ? 'active' : '';
                return `
                    <button type="button" class="module-option ${active}" data-module="${esc(mod)}">
                        <span>${esc(normalizeModuleName(mod))}</span>
                        <span>${count}</span>
                    </button>
                `;
            })
            .join('');

        moduleMenu.querySelectorAll('.module-option').forEach((btn) => {
            btn.addEventListener('click', () => {
                state.activeModule = btn.dataset.module;
                setMenuOpen(false);
                render();
            });
        });
    }

    function render() {
        if (!state.activeModule && state.modules.length) {
            state.activeModule = state.modules[0];
        }

        const moduleName = state.activeModule || 'Commands';
        titleEl.textContent = normalizeModuleName(moduleName);
        moduleToggleLabel.textContent = normalizeModuleName(moduleName);
        currentIcon.className = `fas ${moduleIcon(moduleName)}`;

        const fullList = state.byModule[moduleName] || [];
        const filtered = filterRecursive(fullList, state.query.trim().toLowerCase());
        const count = countCommandsRecursive(filtered);
        countEl.textContent = `${count} commands`;
        const flatCards = flattenCommands(filtered);

        if (!flatCards.length) {
            grid.innerHTML = '<div class="empty">No commands found for this filter.</div>';
        } else {
            grid.innerHTML = flatCards
                .map((cmd) => `<article class="command-card ${cmd._isSub ? 'sub-card' : ''}">${renderCommandCard(cmd)}</article>`)
                .join('');
        }

        grid.querySelectorAll('[data-copy]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const value = btn.getAttribute('data-copy') || '';
                try {
                    await navigator.clipboard.writeText(value);
                    btn.classList.remove('fa-copy');
                    btn.classList.add('fa-check');
                    setTimeout(() => {
                        btn.classList.remove('fa-check');
                        btn.classList.add('fa-copy');
                    }, 900);
                } catch (_) {}
            });
        });

        renderDock();
        renderModuleMenu();
    }

    async function init() {
        try {
            const res = await fetch('/api/commands', { cache: 'no-store' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const payload = await res.json();

            if (Array.isArray(payload)) {
                state.byModule = { Commands: payload };
            } else {
                state.byModule = payload || {};
            }
            state.modules = Object.keys(state.byModule).sort((a, b) => a.localeCompare(b));
            state.activeModule = state.modules[0] || null;

            render();
        } catch (err) {
            console.error('Failed to load commands:', err);
            grid.innerHTML = '<div class="empty">Failed to load commands. Please try again.</div>';
            countEl.textContent = '0 commands';
        }
    }

    searchInput.addEventListener('input', (e) => {
        state.query = e.target.value || '';
        render();
    });

    moduleToggle.addEventListener('click', () => {
        const expanded = moduleToggle.getAttribute('aria-expanded') === 'true';
        setMenuOpen(!expanded);
    });

    document.addEventListener('click', (e) => {
        if (!e.target.closest('#navSelect')) {
            setMenuOpen(false);
        }
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') setMenuOpen(false);
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            searchInput.focus();
        }
    });

    document.addEventListener('DOMContentLoaded', init);
})();