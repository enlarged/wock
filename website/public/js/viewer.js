async function syncBranding() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        if (!data.avatar) return;
        document.getElementById('favicon').href = data.avatar;
        document.getElementById('brandAvatar').src = data.avatar;
        document.getElementById('footerAvatar').src = data.avatar;
    } catch (_) {}
}

syncBranding();
