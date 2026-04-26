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
