// app/static/app.js
// -----------------------------------------------------------------------------
// IMPORTANT (2026-01):
// Dieses Script wurde bewusst deaktiviert, weil es global in layout.html geladen
// wird und auf Seiten wie tournament_participants.html (mit #hitsBody) mit
// participants_keys.js kollidiert (doppelte Arrow/Enter-Handler).
//
// Wenn du dieses Verhalten zukünftig wieder brauchst, lade eine page-spezifische
// Version ODER aktiviere es nur, wenn z.B. <body data-page="..."> gesetzt ist.
// -----------------------------------------------------------------------------

(function () {
  return; // ✅ NO-OP (deaktiviert)
})();