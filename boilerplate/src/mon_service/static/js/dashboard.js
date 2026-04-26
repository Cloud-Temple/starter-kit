/**
 * dashboard.js — Page Dashboard de la console admin.
 *
 * Affiche l'état de santé du service, la version, les outils disponibles.
 * Dépend de : config.js, api.js
 */

async function loadDashboard() {
    const div = document.getElementById('page-dashboard');
    div.innerHTML = '<p class="muted" style="padding:1rem">Chargement…</p>';

    const d = await apiGet('/health');
    if (d.status === 'error') {
        div.innerHTML = `<div class="card"><p style="color:var(--danger)">❌ ${d.message}</p></div>`;
        return;
    }

    // Mettre à jour le header dynamiquement
    const titleEl = document.getElementById('headerTitle');
    if (titleEl && d.service_name) titleEl.textContent = d.service_name;
    const versionEl = document.getElementById('headerVersion');
    if (versionEl) versionEl.textContent = `v${d.version || 'dev'}`;

    // Badge S3
    const s3Badge = d.s3_configured
        ? '<span class="badge badge-ok">✅ Configuré</span>'
        : '<span class="badge badge-warn">⚠️ Non configuré</span>';

    // Liste des outils
    const toolsHtml = (d.tools && d.tools.length)
        ? d.tools.map(t => `<span class="tool-tag">${t}</span>`).join('')
        : '<span class="muted">Aucun outil enregistré</span>';

    div.innerHTML = `
        <h2 class="page-title">📊 Dashboard</h2>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">${d.tools_count || 0}</div>
                <div class="stat-label">Outils MCP</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${d.version || 'dev'}</div>
                <div class="stat-label">Version</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="font-size:1.2rem">
                    ${d.s3_configured ? '✅' : '⚠️'}
                </div>
                <div class="stat-label">S3 Token Store</div>
            </div>
        </div>

        <div class="card">
            <h2>État du service</h2>
            <table>
                <tr>
                    <td style="color:var(--text2); width:140px">Service</td>
                    <td><strong>${d.service_name || '?'}</strong></td>
                </tr>
                <tr>
                    <td style="color:var(--text2)">Version</td>
                    <td>${d.version || '?'}</td>
                </tr>
                <tr>
                    <td style="color:var(--text2)">Python</td>
                    <td>${d.python_version || '?'}</td>
                </tr>
                <tr>
                    <td style="color:var(--text2)">S3</td>
                    <td>${s3Badge}</td>
                </tr>
                <tr>
                    <td style="color:var(--text2)">Statut</td>
                    <td><span class="badge badge-ok">✅ En ligne</span></td>
                </tr>
            </table>
        </div>

        <div class="card mt-1">
            <h2>🔧 Outils MCP (${d.tools_count || 0})</h2>
            <div class="tools-list">${toolsHtml}</div>
        </div>
    `;
}
