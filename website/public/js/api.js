/**
 * Toggles the visibility of the interactive testing panel
 */
function toggleTest(id) {
    const panel = document.getElementById(id);
    panel.classList.toggle('active');
}

/**
 * Handles switching between cURL, JS, and Python code examples
 */
function switchTab(event, blockId) {
    event.stopPropagation();
    const wrapper = event.target.closest('.example-section');
    
    wrapper.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');

    wrapper.querySelectorAll('.code-block').forEach(block => block.classList.remove('active'));
    document.getElementById(blockId).classList.add('active');
}

/**
 * Executes a live API request and displays the result
 */
async function runTest(path, id, hasAuth, params) {
    const resDiv = document.getElementById(`res-${id}`);
    resDiv.style.color = '#adbac7';
    resDiv.innerText = '// Processing request...';

    let finalPath = path;
    const headers = { 'Content-Type': 'application/json' };

    if (hasAuth) {
        const keyInput = document.getElementById(`key-${id}`);
        const key = keyInput ? keyInput.value : null;
        
        if(!key) {
            resDiv.style.color = '#ff6b6b';
            resDiv.innerText = '// Error: API Key is required for this endpoint.';
            return;
        }
        headers['x-api-key'] = key;
    }

    if (params && params.length > 0) {
        const query = new URLSearchParams();
        params.forEach(p => {
            const input = document.getElementById(`param-${p}-${id}`);
            const val = input ? input.value : null;
            if (val) query.append(p, val);
        });
        const queryString = query.toString();
        if(queryString) finalPath += `?${queryString}`;
    }

    try {
        const start = performance.now();
        const response = await fetch(finalPath, { headers });
        const data = await response.json();
        const end = performance.now();

        const statusColor = response.ok ? '#10b981' : '#ff6b6b';
        
        resDiv.innerHTML = `<span style="color: #666666">// Status: <span style="color: ${statusColor}">${response.status} ${response.statusText}</span> | Time: ${Math.round(end - start)}ms</span>\n\n` + 
                           JSON.stringify(data, null, 4);
    } catch (err) {
        resDiv.style.color = '#ff6b6b';
        resDiv.innerText = '// Fetch Error: ' + err.message;
    }
}