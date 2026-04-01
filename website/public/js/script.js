function renderCommands() {
    const query = searchBox.value.toLowerCase();
    commandList.innerHTML = '';
    let pool = [];

    if (currentCategory === 'All') {
        Object.values(commandsJSON).forEach(m => pool = pool.concat(m));
    } else {
        pool = commandsJSON[currentCategory] || [];
    }

    pool.forEach(cmd => {
        if (cmd.name.toLowerCase().includes(query)) {
            renderCard(
                cmd.name, 
                cmd.description, 
                cmd.syntax || 'none', 
                cmd.permissions || 'none'
            );
        }

        if (cmd.subcommands && cmd.subcommands.length > 0) {
            cmd.subcommands.forEach(sub => {
                const fullName = `${cmd.name} ${sub.name}`;
                if (fullName.toLowerCase().includes(query)) {
                    renderCard(
                        fullName, 
                        sub.description || cmd.description, // Implies main description if sub is missing it
                        sub.syntax || 'none', 
                        sub.permissions || cmd.permissions || 'none'
                    );
                }
            });
        }
    });
}

function renderCard(name, desc, syntax, perm) {
    const card = document.createElement('div');
    card.className = 'command-card';
    card.innerHTML = `
        <div class="card-header">
            <div class="cmd-name">${name}</div>
            <div class="copy-icon" onclick="navigator.clipboard.writeText(',${name}')">📋</div>
        </div>
        <div class="cmd-desc">${desc}</div>
        
        <div class="meta-section">
            <span class="meta-label">syntax</span>
            <div class="meta-value">${syntax}</div>
        </div>

        <div class="meta-section">
            <span class="meta-label">permissions</span>
            <div class="tag-perm">${perm}</div>
        </div>
    `;
    commandList.appendChild(card);
}