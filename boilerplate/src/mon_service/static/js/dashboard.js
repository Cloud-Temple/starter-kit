/**
 * dashboard.js — Page Dashboard de la console admin.
 *
 * Affiche l'état de santé du service, la version, les outils disponibles.
 * Rendu via createElement + textContent (helper `el` de config.js) — cohérent
 * avec activity.js/tokens.js : aucune interpolation de données en innerHTML.
 * Dépend de : config.js (el, apiGet…)
 */

async function loadDashboard() {
    const div = document.getElementById('page-dashboard');
    div.replaceChildren(el('p', 'muted', 'Chargement…'));

    const d = await apiGet('/health');
    if (!d || d.status === 'error') {
        const card = el('div', 'card');
        const p = el('p', null, `❌ ${(d && d.message) || 'Service indisponible'}`);
        p.style.color = 'var(--danger)';
        card.appendChild(p);
        div.replaceChildren(card);
        return;
    }

    // Mettre à jour le header dynamiquement
    const titleEl = document.getElementById('headerTitle');
    if (titleEl && d.service_name) titleEl.textContent = d.service_name;
    const versionEl = document.getElementById('headerVersion');
    if (versionEl) versionEl.textContent = `v${d.version || 'dev'}`;

    div.replaceChildren();
    div.appendChild(el('h2', 'page-title', '📊 Dashboard'));

    // Cartes de stats
    const grid = el('div', 'stats-grid');
    grid.appendChild(_statCard(d.tools_count || 0, 'Outils MCP'));
    grid.appendChild(_statCard(d.version || 'dev', 'Version'));
    const s3card = _statCard(d.s3_configured ? '✅' : '⚠️', 'S3 Token Store');
    s3card.querySelector('.stat-value').style.fontSize = '1.2rem';
    grid.appendChild(s3card);
    div.appendChild(grid);

    // État du service
    const card = el('div', 'card');
    card.appendChild(el('h2', null, 'État du service'));
    const table = el('table');
    table.appendChild(_kvRow('Service', d.service_name || '?', true));
    table.appendChild(_kvRow('Version', d.version || '?'));
    table.appendChild(_kvRow('Python', d.python_version || '?'));

    const s3Row = el('tr');
    s3Row.appendChild(_kvLabel('S3'));
    const s3td = el('td');
    s3td.appendChild(d.s3_configured
        ? el('span', 'badge badge-ok', '✅ Configuré')
        : el('span', 'badge badge-warn', '⚠️ Non configuré'));
    s3Row.appendChild(s3td);
    table.appendChild(s3Row);

    const stRow = el('tr');
    stRow.appendChild(_kvLabel('Statut'));
    const sttd = el('td');
    sttd.appendChild(el('span', 'badge badge-ok', '✅ En ligne'));
    stRow.appendChild(sttd);
    table.appendChild(stRow);

    card.appendChild(table);
    div.appendChild(card);

    // Outils MCP
    const toolsCard = el('div', 'card mt-1');
    toolsCard.appendChild(el('h2', null, `🔧 Outils MCP (${d.tools_count || 0})`));
    const list = el('div', 'tools-list');
    const tools = Array.isArray(d.tools) ? d.tools : [];
    if (tools.length) {
        tools.forEach(t => list.appendChild(
            el('span', 'tool-tag', typeof t === 'string' ? t : (t && t.name) || String(t))));
    } else {
        list.appendChild(el('span', 'muted', 'Aucun outil enregistré'));
    }
    toolsCard.appendChild(list);
    div.appendChild(toolsCard);
}

function _statCard(value, label) {
    const c = el('div', 'stat-card');
    c.appendChild(el('div', 'stat-value', value));
    c.appendChild(el('div', 'stat-label', label));
    return c;
}

function _kvLabel(text) {
    const td = el('td', null, text);
    td.style.color = 'var(--text2)';
    return td;
}

function _kvRow(label, value, strong) {
    const tr = el('tr');
    tr.appendChild(_kvLabel(label));
    const td = el('td');
    if (strong) td.appendChild(el('strong', null, value));
    else td.textContent = String(value);
    tr.appendChild(td);
    return tr;
}
