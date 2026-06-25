/**
 * tokens.js — Gestion des tokens d'accès.
 *
 * Liste, création et révocation des tokens via l'API admin.
 * Rendu via createElement + textContent (helper `el` de config.js) : les champs
 * de token (client_name, email…) sont saisis par l'utilisateur → jamais injectés
 * en innerHTML. Les actions (révoquer / nouveau) passent par `data-action`
 * (délégation dans app.js), pas par des handlers inline (compatibles CSP stricte).
 * Dépend de : config.js (el, apiGet…), app.js (openModal/closeModal)
 */

async function loadTokens() {
    const div = document.getElementById('page-tokens');
    div.replaceChildren(el('p', 'muted', 'Chargement…'));

    const d = await apiGet('/tokens');

    // En-tête + bouton "Nouveau token" (action déléguée, pas d'onclick inline)
    const header = el('div', 'flex-between mb-1');
    header.appendChild(el('h2', 'page-title', "🔑 Tokens d'accès"));
    const newBtn = el('button', 'btn btn-primary', '+ Nouveau token');
    newBtn.dataset.action = 'openCreateToken';
    header.appendChild(newBtn);

    const card = el('div', 'card');

    if (d && d.status === 'error') {
        card.appendChild(el('p', 'empty-state', `Erreur : ${d.message || 'inconnue'}`));
    } else if (!d || !Array.isArray(d.tokens) || d.tokens.length === 0) {
        card.appendChild(el('p', 'empty-state', 'Aucun token (S3 non configuré ou liste vide)'));
    } else {
        card.appendChild(_buildTokenTable(d.tokens));
    }

    div.replaceChildren(header, card);
}

/** Construit le tableau des tokens en DOM (aucune interpolation HTML). */
function _buildTokenTable(tokens) {
    const table = el('table');
    const thead = el('thead');
    const htr = el('tr');
    ['Client', 'Email', 'Permissions', 'Hash', 'Expiration', 'Statut', 'Actions']
        .forEach(h => htr.appendChild(el('th', null, h)));
    thead.appendChild(htr);
    table.appendChild(thead);

    const tbody = el('tbody');
    tokens.forEach(t => {
        try {
            tbody.appendChild(_buildTokenRow(t));
        } catch (_e) {
            // garde par-ligne : un token malformé n'efface pas tout le tableau
        }
    });
    table.appendChild(tbody);
    return table;
}

function _buildTokenRow(t) {
    const tr = el('tr');

    const tdClient = el('td');
    tdClient.appendChild(el('strong', null, t.client_name || '?'));
    tr.appendChild(tdClient);

    tr.appendChild(el('td', null, t.email || '—'));
    tr.appendChild(el('td', null, (t.permissions || []).join(', ')));

    const tdHash = el('td');
    tdHash.appendChild(el('code', 'mono-sm', `${(t.hash_prefix || '?').substring(0, 12)}…`));
    tr.appendChild(tdHash);

    tr.appendChild(el('td', null, t.expires_at ? t.expires_at.substring(0, 10) : 'jamais'));

    const tdStatus = el('td');
    tdStatus.appendChild(t.revoked
        ? el('span', 'badge badge-err', 'Révoqué')
        : el('span', 'badge badge-ok', 'Actif'));
    tr.appendChild(tdStatus);

    const tdAction = el('td');
    if (!t.revoked && t.hash_prefix) {
        const btn = el('button', 'btn btn-danger btn-sm', 'Révoquer');
        btn.dataset.action = 'revokeToken';
        btn.dataset.hash = t.hash_prefix;   // dataset = valeur littérale, jamais exécutée
        tdAction.appendChild(btn);
    }
    tr.appendChild(tdAction);

    return tr;
}

/**
 * Ouvre le modal de création de token.
 */
function openCreateToken() {
    document.getElementById('ctName').value = '';
    document.getElementById('ctEmail').value = '';
    document.getElementById('ctExpires').value = '90';
    document.getElementById('ctPermWrite').checked = false;
    document.getElementById('ctPermAdmin').checked = false;
    openModal('modalCreateToken');
}

/**
 * Envoie la requête de création de token.
 */
async function doCreateToken() {
    const name = document.getElementById('ctName').value.trim();
    if (!name) {
        alert('Le nom du client est requis.');
        return;
    }

    const perms = ['read'];
    if (document.getElementById('ctPermWrite').checked) perms.push('write');
    if (document.getElementById('ctPermAdmin').checked) perms.push('admin');

    const result = await apiPost('/tokens', {
        client_name: name,
        email: document.getElementById('ctEmail').value.trim(),
        permissions: perms,
        expires_in_days: parseInt(document.getElementById('ctExpires').value) || 90,
    });

    closeModal('modalCreateToken');

    if (result.raw_token) {
        // Afficher le token dans le modal de résultat (textContent → safe)
        document.getElementById('tokenResultValue').textContent = result.raw_token;
        const meta = [
            `Client : ${result.client_name || name}`,
            `Permissions : ${(result.permissions || perms).join(', ')}`,
            `Expire : ${result.expires_at ? result.expires_at.substring(0, 10) : 'jamais'}`,
        ].join(' · ');
        document.getElementById('tokenResultMeta').textContent = meta;
        openModal('modalTokenResult');
        await loadTokens();
    } else {
        alert('Erreur : ' + (result.message || JSON.stringify(result)));
    }
}

/**
 * Copie le token dans le presse-papier.
 */
function copyToken() {
    const val = document.getElementById('tokenResultValue').textContent;
    navigator.clipboard.writeText(val).then(() => {
        const btn = document.querySelector('#modalTokenResult .copy-btn');
        if (btn) { btn.textContent = '✓ Copié'; setTimeout(() => { btn.textContent = 'Copier'; }, 2000); }
    });
}

/**
 * Révoque un token par préfixe de hash.
 */
async function revokeToken(hashPrefix) {
    const short = hashPrefix.substring(0, 12);
    if (!confirm(`Révoquer le token ${short}… ?\n\nCette action est irréversible.`)) return;

    const result = await apiDelete(`/tokens/${hashPrefix}`);
    if (result.status === 'ok') {
        await loadTokens();
    } else {
        alert('Erreur : ' + (result.message || JSON.stringify(result)));
    }
}
