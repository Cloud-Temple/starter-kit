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
// Branding
// =============================================================================

async function loadBranding() {
    const brand = await apiGet('/brand');
    if (!brand || brand.status === 'error') return;

    const root = document.documentElement;
    const colors = brand.colors || {};
    if (colors.bg) root.style.setProperty('--bg', colors.bg);
    if (colors.surface) root.style.setProperty('--surface', colors.surface);
    if (colors.accent) root.style.setProperty('--accent', colors.accent);
    if (colors.accent_hover) root.style.setProperty('--accent-hover', colors.accent_hover);

    const title = `${brand.company_name || 'Cloud Temple'} — ${brand.app_title || 'Admin Console'}`;
    document.title = title;

    const loginLogo = document.getElementById('loginLogo');
    const headerLogo = document.getElementById('headerLogo');
    if (brand.logo) {
        if (loginLogo) loginLogo.src = brand.logo;
        if (headerLogo) headerLogo.src = brand.logo;
    }
    if (loginLogo) loginLogo.alt = brand.company_name || 'Brand logo';
    if (headerLogo) headerLogo.alt = brand.company_name || 'Brand logo';

    const headerTitle = document.getElementById('headerTitle');
    if (headerTitle) headerTitle.textContent = brand.app_title || 'Admin Console';
}


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

// Délégation d'événements : navigation (data-page) + actions (data-action).
// Remplace les handlers inline `onclick=` (incompatibles avec une CSP stricte
// `script-src 'self'`, qui est notre filet anti-XSS).
document.addEventListener('click', e => {
    // Fermeture d'un modal en cliquant sur son fond (overlay)
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
        return;
    }
    // Navigation sidebar
    const nav = e.target.closest('[data-page]');
    if (nav) { showPage(nav.dataset.page); return; }
    // Actions déclaratives
    const act = e.target.closest('[data-action]');
    if (!act) return;
    switch (act.dataset.action) {
        case 'logout': logout(); break;
        case 'openCreateToken': openCreateToken(); break;
        case 'doCreateToken': doCreateToken(); break;
        case 'copyToken': copyToken(); break;
        case 'closeModal': closeModal(act.dataset.arg); break;
        case 'revokeToken': revokeToken(act.dataset.hash); break;
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
    loadBranding();

    // Pré-remplir le token depuis localStorage si disponible
    const saved = localStorage.getItem('mcp_admin_token');
    if (saved) {
        document.getElementById('loginToken').value = saved;
        // Option : auto-login si token sauvegardé
        // document.getElementById('loginForm').dispatchEvent(new Event('submit'));
    }
})();
