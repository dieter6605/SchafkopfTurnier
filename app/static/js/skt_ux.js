// app/static/js/skt_ux.js
(function () {
  function ensureBootstrapToast() {
    // Bootstrap 5 erwartet global "bootstrap"
    if (!window.bootstrap || !window.bootstrap.Toast) return null;
    const el = document.getElementById("sktToast");
    if (!el) return null;
    return new window.bootstrap.Toast(el, { delay: 4500 });
  }

  function showToast(message) {
    const body = document.getElementById("sktToastBody");
    if (body) body.textContent = message || "Aktion nicht möglich.";
    const t = ensureBootstrapToast();
    if (t) t.show();
    // Fallback: wenn Bootstrap Toast nicht verfügbar ist
    else alert(message || "Aktion nicht möglich.");
  }

  // Global verfügbar (optional praktisch)
  window.SKT = window.SKT || {};
  window.SKT.toast = showToast;

  // fetch patchen: bei 409 => Toast, Promise reject
  const _origFetch = window.fetch;
  window.fetch = async function (...args) {
    const resp = await _origFetch.apply(this, args);

    if (resp && resp.status === 409) {
      let msg = "Dieses Turnier ist abgeschlossen – Änderung nicht möglich.";

      // Versuch: JSON lesen
      const ctype = (resp.headers.get("content-type") || "").toLowerCase();
      try {
        if (ctype.includes("application/json")) {
          const data = await resp.clone().json();
          if (data && (data.error || data.message)) msg = data.error || data.message;
        } else {
          // sonst Text lesen (kurz halten)
          const txt = await resp.clone().text();
          if (txt && txt.length < 300) msg = txt;
        }
      } catch (e) {
        // ignorieren
      }

      showToast(msg);

      // wichtig: Fehler weiterwerfen, damit Caller weiß, dass es nicht geklappt hat
      const err = new Error(msg);
      err.status = 409;
      throw err;
    }

    return resp;
  };

  // Optional: unhandled promise rejections abfangen (wenn Code fetch nicht catcht)
  window.addEventListener("unhandledrejection", function (ev) {
    const r = ev.reason;
    if (r && r.status === 409) {
      // Toast wurde schon gezeigt
      ev.preventDefault();
    }
  });
})();