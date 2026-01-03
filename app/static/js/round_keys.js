// app/static/js/round_keys.js
(function () {
  // Guard: nicht doppelt initialisieren
  if (window.__SKT_ROUND_KEYS_INIT__) return;
  window.__SKT_ROUND_KEYS_INIT__ = true;

  // Debug (optional): window.__SKT_DEBUG_KEYS__ = 1;
  function dbg(...args) {
    try {
      if (window.__SKT_DEBUG_KEYS__ === 1 || window.__SKT_DEBUG_KEYS__ === true) {
        // eslint-disable-next-line no-console
        console.log("[SKT][round]", ...args);
      }
    } catch (_) { /* ignore */ }
  }

  function isTypingContext() {
    const el = document.activeElement;
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function isDisabled(el) {
    if (!el) return true;
    if (el.classList && el.classList.contains("disabled")) return true;
    if (el.getAttribute && el.getAttribute("aria-disabled") === "true") return true;
    if ("disabled" in el && el.disabled === true) return true;
    if (el.hasAttribute && el.hasAttribute("disabled")) return true;
    return false;
  }

  function isClosed() {
    try {
      return (window.__SKT_TOURNAMENT_CLOSED__ === 1 ||
              window.__SKT_TOURNAMENT_CLOSED__ === true);
    } catch (e) {
      return false;
    }
  }

  function clickIfEnabled(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    if (isDisabled(el)) return false;
    dbg("click", id);
    el.click();
    return true;
  }

  function goNextOrPrepare() {
    // Wenn "Nächste" existiert -> dahin, sonst "Vorbereiten"
    if (clickIfEnabled("btnNextRound")) return true;
    return clickIfEnabled("btnPrepareNext");
  }

  function allowDrawNext() {
    try {
      return (window.__SKT_ALLOW_DRAW_NEXT__ === 1 ||
              window.__SKT_ALLOW_DRAW_NEXT__ === true);
    } catch (e) {
      return false;
    }
  }

  function init() {
    // Wenn auf einer Seite ohne Round-Controls: nicht global lauschen
    const hasAny =
      document.getElementById("btnBackTournament") ||
      document.getElementById("btnPrevRound") ||
      document.getElementById("btnNextRound") ||
      document.getElementById("btnPrepareNext") ||
      document.getElementById("btnRedraw") ||
      document.getElementById("btnDrawNext") ||
      document.getElementById("btnLastRound");

    if (!hasAny) return;

    dbg("init()", { closed: isClosed(), allowDrawNext: allowDrawNext() });

    document.addEventListener("keydown", function (ev) {
      if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
      if (isTypingContext()) return;

      const key = String(ev.key || "");
      const k = key.toLowerCase();

      dbg("keydown", {
        key,
        shift: !!ev.shiftKey,
        typing: isTypingContext(),
        closed: isClosed(),
        allowDrawNext: allowDrawNext()
      });

      // ESC = zurück zum Turnier
      if (key === "Escape") {
        ev.preventDefault();
        clickIfEnabled("btnBackTournament");
        return;
      }

      // Pfeile: ←/→ = Vor/Zurück (→ nutzt Vorbereiten, wenn Nächste nicht existiert)
      if (key === "ArrowLeft") {
        ev.preventDefault();
        clickIfEnabled("btnPrevRound");
        return;
      }
      if (key === "ArrowRight") {
        ev.preventDefault();
        goNextOrPrepare();
        return;
      }

      // T = Turnier
      if (k === "t") {
        ev.preventDefault();
        clickIfEnabled("btnBackTournament");
        return;
      }

      // P / N = Vor/Zurück (N nutzt Vorbereiten, wenn Nächste nicht existiert)
      if (k === "p") {
        ev.preventDefault();
        clickIfEnabled("btnPrevRound");
        return;
      }
      if (k === "n") {
        ev.preventDefault();
        goNextOrPrepare();
        return;
      }

      // R / Shift+R nur wenn NICHT geschlossen
      if (k === "r") {
        if (isClosed()) return; // Turnier abgeschlossen -> keine Aktion

        ev.preventDefault();

        if (ev.shiftKey) {
          if (allowDrawNext() && clickIfEnabled("btnDrawNext")) return;
          // Fallback: aktuelle Runde auslosen
          clickIfEnabled("btnRedraw");
          return;
        }

        clickIfEnabled("btnRedraw"); // confirm hängt am formDraw
        return;
      }

      // L = letzte Runde
      if (k === "l") {
        ev.preventDefault();
        clickIfEnabled("btnLastRound");
        return;
      }
    }, { passive: false });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();