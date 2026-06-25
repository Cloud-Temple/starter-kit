/**
 * activity.js — Page Activité (ring buffer de logs).
 *
 * Rendu via createElement + textContent (jamais innerHTML avec des données) :
 * les entrées du ring buffer proviennent de requêtes HTTP arbitraires
 * (path/méthode non fiables) — textContent n'interprète jamais le HTML, ce qui
 * neutralise toute injection (XSS stocké) par construction.
 * Dépend de : config.js, api.js
 */

let _activityTimer = null;

// Helper DOM partagé `el()` défini dans config.js (rendu via textContent, anti-XSS).

/** Extrait HH:MM:SS d'un timestamp ISO 8601 UTC (format émis par le serveur). */
function _fmtTime(ts) {
    if (typeof ts !== 'string') return '';
    const s = ts.replace('T', ' ');
    return s.length >= 19 ? s.substring(11, 19) : s;
}

/** Construit une ligne de log (DOM). Peut throw sur une entrée malformée → géré par l'appelant. */
function _buildLogEntry(l) {
    const statusCode = Number(l.status) || 0;
    const statusClass = statusCode >= 500 ? 's5xx' : statusCode >= 400 ? 's4xx' : 's2xx';
    // méthode restreinte à [A-Z] pour la classe CSS (le texte affiché reste posé en textContent)
    const method = String(l.method || 'GET').toUpperCase();
    const methodClass = method.replace(/[^A-Z]/g, '') || 'GET';
    const duration = l.duration_ms !== undefined ? `${l.duration_ms}ms` : '';
    const timeShort = _fmtTime(l.timestamp ?? l.ts);

    const row = el('div', 'log-entry');
    row.appendChild(el('span', `log-method ${methodClass}`, method));
    row.appendChild(el('span', 'log-path', l.path || '?'));
    row.appendChild(el('span', `log-status ${statusClass}`, statusCode));
    row.appendChild(el('span', 'log-time', timeShort + (duration ? ' · ' + duration : '')));
    return row;
}

async function loadActivity() {
    const div = document.getElementById('page-activity');
    if (!div) return;

    let d;
    try {
        d = await apiGet('/logs');
    } catch (e) {
        d = { status: 'error', message: (e && e.message) ? e.message : String(e) };
    }

    div.replaceChildren();  // vide le panneau sans innerHTML

    const count = Number(d && d.count) || 0;
    const header = el('div', 'flex-between mb-1');
    header.appendChild(el('h2', 'page-title', `📋 Activité récente (${count} requêtes)`));
    const refresh = el('span', 'muted', '⏱ Auto-refresh 5s');
    refresh.style.fontSize = '0.78rem';
    header.appendChild(refresh);
    div.appendChild(header);

    const card = el('div', 'card');
    card.style.padding = '0.4rem 0';

    const logs = (d && Array.isArray(d.logs)) ? d.logs : [];
    if (logs.length === 0) {
        const msg = (d && d.status === 'error')
            ? `Erreur : ${(d.message || 'inconnue')}`
            : 'Aucune activité récente';
        card.appendChild(el('p', 'empty-state', msg));
    } else {
        // Plus récent en premier. Garde PAR ENTRÉE : une entrée malformée
        // n'efface pas tout le panneau (cf. bug "page blanche").
        for (let i = logs.length - 1; i >= 0; i--) {
            try {
                card.appendChild(_buildLogEntry(logs[i]));
            } catch (_e) {
                // entrée ignorée silencieusement
            }
        }
    }
    div.appendChild(card);
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
