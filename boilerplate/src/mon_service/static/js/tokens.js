/**
 * tokens.js — Gestion des tokens d'accès.
 *
 * Liste, création et révocation des tokens via l'API admin.
 * Dépend de : config.js, api.js, app.js (openModal/closeModal)
 */

async function loadTokens() {
    const div = document.getElementById('page-tokens');
    div.innerHTML = '<p class="muted" style="padding:1rem">Chargement…</p>';

    const d = await apiGet('/tokens');

    let tableHtml = '';
    if (!d.tokens || d.tokens.length === 0) {
        tableHtml = '<p class="empty-state">Aucun token (S3 non configuré ou liste vide)</p>';
    } else {
        const rows = d.tokens.map(t => {
            const status = t.revoked
                ? '<span class="badge badge-err">Révoqué</span>'
                : '<span class="badge badge-ok">Actif</span>';
            const perms = (t.permissions || []).join(', ');
            const hash = (t.hash_prefix || '?').substring(0, 12);
            const exp = t.expires_at
                ? t.expires_at.substring(0, 10)
                : '<span class="muted">jamais</span>';
            const email = t.email || '<span class="muted">—</span>';
            const revokeBtn = !t.revoked
                ? `<button class="btn btn-danger btn-sm" onclick="revokeToken('${t.hash_prefix}')">Révoquer</button>`
                : '';

            return `<tr>
                <td><strong>${t.client_name || '?'}</strong></td>
                <td>${email}</td>
                <td>${perms}</td>
                <td><code style="font-size:0.75rem; color:var(--muted)">${hash}…</code></td>
                <td>${exp}</td>
                <td>${status}</td>
                <td>${revokeBtn}</td>
            </tr>`;
        }).join('');

        tableHtml = `
            <table>
                <thead>
                    <tr>
                        <th>Client</th>
                        <th>Email</th>
                        <th>Permissions</th>
                        <th>Hash</th>
                        <th>Expiration</th>
                        <th>Statut</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    div.innerHTML = `
        <div class="flex-between mb-1">
            <h2 class="page-title">🔑 Tokens d'accès</h2>
            <button class="btn btn-primary" onclick="openCreateToken()">+ Nouveau token</button>
        </div>
        <div class="card">${tableHtml}</div>
    `;
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
        // Afficher le token dans le modal de résultat
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
