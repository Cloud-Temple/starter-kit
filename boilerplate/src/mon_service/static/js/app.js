/**
 * app.js — Application principale de la console admin.
 *
 * Gère :
 * - L'authentification (login / logout)
 * - La navigation entre pages (sidebar)
 * - Les modals
 * - La persistance du token dans localStorage
 *
 * Dépend de : config.js, api.js, dashboard.js, tokens.js, activity.js
 * Ce fichier est chargé EN DERNIER (après tous les autres scripts).
 */

// =============================================================================
// Navigation
// =============================================================================

/**
 * Affiche une page et masque les autres.
 * Met à jour la sidebar et charge les données de la page.
 *
 * @param {string} name - Nom de la page (dashboard, tokens, activity, ...)
 */
function showPage(name) {
    // Masquer toutes les pages
    document.querySelectorAll('.main-content > [id^="page-"]').forEach(p => {
        p.classList.add('hidden');
    });

    // Désactiver tous les boutons sidebar
    document.querySelectorAll('.sidebar-nav button').forEach(b => {
        b.classList.remove('active');
    });

    // Afficher la page cible
    const page = document.getElementById(`page-${name}`);
    if (page) page.classList.remove('hidden');

    // Activer le bouton sidebar correspondant
    const btn = document.querySelector(`[data-page="${name}"]`);
    if (btn) btn.classList.add('active');

    // Charger les données de la page
    if (name === 'dashboard') loadDashboard();
    else if (name === 'tokens') loadTokens();
    else if (name === 'activity') loadActivity();
    // Ajouter vos pages métier ici :
    // else if (name === 'ma-page') loadMaPage();
}


// =============================================================================
// Modals
// =============================================================================

/**
 * Ouvre un modal.
 * @param {string} id - ID de l'élément modal-overlay
 */
function openModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.add('active');
}

/**
 * Ferme un modal.
 * @param {string} id - ID de l'élément modal-overlay
 */
function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('active');
}

// Fermer un modal en cliquant sur son fond (overlay)
document.addEventListener('click', e => {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
    }
});

// Fermer un modal avec Escape
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.active').forEach(m => {
            m.classList.remove('active');
        });
    }
});


// =============================================================================
// Authentification
// =============================================================================

/**
 * Affiche un message d'erreur sur la page de login.
 * @param {string} msg - Message d'erreur
 */
function showLoginError(msg) {
    const el = document.getElementById('loginError');
    if (el) el.textContent = msg;
}

/**
 * Déconnecte l'utilisateur et affiche la page de login.
 */
function logout() {
    AUTH_TOKEN = '';
    localStorage.removeItem('mcp_admin_token');
    stopActivityRefresh();

    document.getElementById('appMain').classList.add('hidden');
    document.getElementById('loginOverlay').classList.remove('hidden');
    document.getElementById('loginToken').value = '';
    showLoginError('');
}

/**
 * Finalise le login après validation du token.
 * @param {string} token - Token validé
 */
async function doLogin(token) {
    AUTH_TOKEN = token;

    // Charger l'identité du token
    const me = await apiGet('/whoami');
    if (me.client_name) {
        document.getElementById('headerUser').textContent = me.client_name;
    }

    // Persister le token (commodité, pas de sécurité renforcée)
    localStorage.setItem('mcp_admin_token', token);

    // Afficher l'app
    document.getElementById('loginOverlay').classList.add('hidden');
    document.getElementById('appMain').classList.remove('hidden');

    // Lancer le refresh d'activité
    startActivityRefresh();

    // Afficher le dashboard par défaut
    showPage('dashboard');
}

// Soumission du formulaire de login
document.getElementById('loginForm').addEventListener('submit', async e => {
    e.preventDefault();
    showLoginError('');

    const token = document.getElementById('loginToken').value.trim();
    if (!token) return;

    // Valider le token via l'API health
    AUTH_TOKEN = token;
    const r = await apiGet('/health');

    if (r.status === 'ok' || r.service_name) {
        await doLogin(token);
    } else {
        AUTH_TOKEN = '';
        showLoginError(r.message || 'Token invalide ou service indisponible');
    }
});


// =============================================================================
// Initialisation
// =============================================================================

(function init() {
    // Pré-remplir le token depuis localStorage si disponible
    const saved = localStorage.getItem('mcp_admin_token');
    if (saved) {
        document.getElementById('loginToken').value = saved;
        // Option : auto-login si token sauvegardé
        // document.getElementById('loginForm').dispatchEvent(new Event('submit'));
    }
})();
