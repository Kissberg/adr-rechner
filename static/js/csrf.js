/*
 * csrf.js — ergänzt automatisch den CSRF-Token bei allen fetch-Aufrufen,
 * die Daten ändern (POST/PUT/PATCH/DELETE).
 *
 * Warum ein Wrapper und nicht jeder Aufruf einzeln: die Oberfläche
 * bestreitet jede Seite über mehrere fetch-Stellen (Rechner, Verwaltung,
 * Stammdaten). Ein zentraler Wrapper deckt bestehende und künftige
 * Aufrufstellen ab, ohne dass jede Stelle den Token kennen muss.
 * Formulare ohne JavaScript (die Anmeldung) binden den Token als
 * verstecktes Feld ein (siehe login.html, csrf.py).
 *
 * Der Token steht als <meta name="csrf-token"> im Kopf der Seite
 * (templates/base.html) und wird vom Server nur bei ändernden Requests
 * verlangt — GET/HEAD bleiben unberührt, damit Such- und Leseaufrufe
 * unverändert laufen. Fremde Seiten können den Token nicht lesen und
 * daher keine ändernden Anfrage fälschen (Same-Origin-Policy).
 */
(function () {
    'use strict';

    const TOKEN = (document.querySelector('meta[name="csrf-token"]')
        || {}).content || '';
    const AENDERND = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

    const originalFetch = window.fetch.bind(window);

    window.fetch = function (url, options) {
        options = options || {};
        const methode = (options.method
            || (options.body !== undefined ? 'POST' : 'GET')
        ).toUpperCase();

        if (!AENDERND.has(methode) || !TOKEN) {
            return originalFetch(url, options);
        }

        // Fremde Ziele (falls einmal vorhanden) nicht markieren — der
        // Token gehört ausschließlich zu dieser Anwendung.
        let gleicheHerkunft = true;
        try {
            const ziel = new URL(url, window.location.origin);
            gleicheHerkunft = ziel.origin === window.location.origin;
        } catch (e) {
            /* relatives URL-Schema — bleibt same-origin */
        }
        if (!gleicheHerkunft) {
            return originalFetch(url, options);
        }

        const headers = new Headers(options.headers || {});
        if (!headers.has('X-CSRF-Token')) {
            headers.set('X-CSRF-Token', TOKEN);
        }
        return originalFetch(url, Object.assign({}, options, {headers}));
    };
})();
