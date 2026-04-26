/**
 * api.js — Client HTTP vers l'API REST admin.
 *
 * Fonctions utilitaires pour GET, POST, PUT, DELETE.
 * Toutes les erreurs réseau retournent { status: 'error', message: '...' }.
 *
 * Dépend de : config.js (API_BASE, headers)
 */

async function apiGet(path) {
    try {
        const r = await fetch(`${API_BASE}${path}`, { headers: headers() });
        return r.json();
    } catch (e) {
        return { status: 'error', message: e.message };
    }
}

async function apiPost(path, body) {
    try {
        const r = await fetch(`${API_BASE}${path}`, {
            method: 'POST',
            headers: headers(),
            body: JSON.stringify(body),
        });
        return r.json();
    } catch (e) {
        return { status: 'error', message: e.message };
    }
}

async function apiPut(path, body) {
    try {
        const r = await fetch(`${API_BASE}${path}`, {
            method: 'PUT',
            headers: headers(),
            body: JSON.stringify(body),
        });
        return r.json();
    } catch (e) {
        return { status: 'error', message: e.message };
    }
}

async function apiDelete(path) {
    try {
        const r = await fetch(`${API_BASE}${path}`, {
            method: 'DELETE',
            headers: headers(),
        });
        return r.json();
    } catch (e) {
        return { status: 'error', message: e.message };
    }
}
