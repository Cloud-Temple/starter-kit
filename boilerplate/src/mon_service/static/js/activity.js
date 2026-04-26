/**
 * activity.js — Page Activité (ring buffer de logs).
 *
 * Affiche les requêtes récentes et se rafraîchit automatiquement
 * toutes les 5 secondes quand la page est active.
 * Dépend de : config.js, api.js
 */

let _activityTimer = null;

async function loadActivity() {
    const div = document.getElementById('page-activity');
    if (!div) return;

    const d = await apiGet('/logs');
    const count = d.count || 0;

    let logsHtml = '';
    if (!d.logs || d.logs.length === 0) {
        logsHtml = '<p class="empty-state">Aucune activité récente</p>';
    } else {
        // Les logs arrivent en ordre chronologique, on les affiche du plus récent au plus ancien
        const entries = [...d.logs].reverse();
        logsHtml = entries.map(l => {
            const method = (l.method || 'GET').toUpperCase();
            const statusCode = l.status || 0;
            const statusClass = statusCode >= 500 ? 's5xx' : statusCode >= 400 ? 's4xx' : 's2xx';
            // Timestamp : on extrait HH:MM:SS
            const ts = (l.timestamp || l.ts || '').replace('T', ' ').substring(0, 19);
            const timeShort = ts.length >= 19 ? ts.substring(11, 19) : ts;
            const duration = l.duration_ms !== undefined ? `${l.duration_ms}ms` : '';

            return `<div class="log-entry">
                <span class="log-method ${method}">${method}</span>
                <span class="log-path">${l.path || '?'}</span>
                <span class="log-status ${statusClass}">${statusCode}</span>
                <span class="log-time">${timeShort}${duration ? ' · ' + duration : ''}</span>
            </div>`;
        }).join('');
    }

    div.innerHTML = `
        <div class="flex-between mb-1">
            <h2 class="page-title">📋 Activité récente (${count} requêtes)</h2>
            <span class="muted" style="font-size:0.78rem">⏱ Auto-refresh 5s</span>
        </div>
        <div class="card" style="padding: 0.4rem 0;">
            ${logsHtml}
        </div>
    `;
}

/**
 * Démarre le rafraîchissement automatique (uniquement si la page activité est visible).
 */
function startActivityRefresh() {
    stopActivityRefresh();
    _activityTimer = setInterval(async () => {
        const btn = document.querySelector('[data-page="activity"]');
        if (btn && btn.classList.contains('active')) {
            await loadActivity();
        }
    }, 5000);
}

/**
 * Arrête le rafraîchissement automatique.
 */
function stopActivityRefresh() {
    if (_activityTimer) {
        clearInterval(_activityTimer);
        _activityTimer = null;
    }
}
