/**
 * config.js — Configuration globale de la console admin.
 *
 * Variables globales partagées entre tous les modules JS.
 * Ce fichier est chargé EN PREMIER avant les autres scripts.
 */

// URL de base de l'API admin (même origine, pas de CORS)
const API_BASE = '/admin/api';

// Token d'authentification courant (initialisé par app.js au login)
let AUTH_TOKEN = '';

/**
 * Génère les headers HTTP pour les appels API.
 * Doit être appelé à chaque requête (AUTH_TOKEN peut changer).
 */
function headers() {
    return {
        'Authorization': `Bearer ${AUTH_TOKEN}`,
        'Content-Type': 'application/json',
    };
}

/**
 * Crée un élément DOM. Le texte est posé via `textContent` → jamais interprété
 * comme HTML. Utiliser ce helper (et non `innerHTML` + interpolation) pour rendre
 * des données dynamiques : élimine la classe XSS par construction.
 *
 * @param {string} tag        - nom de balise (ex. 'div', 'span')
 * @param {string} [className] - classes CSS
 * @param {*} [text]          - contenu texte (converti en String, jamais en HTML)
 */
function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
}
