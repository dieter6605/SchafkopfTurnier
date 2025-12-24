// app/static/js/round_keys.js
(function () {
  function isTypingContext() {
    const el = document.activeElement;
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  function isDisabled(el) {
    if (!el) return true;
    if (el.classList && el.classList.contains("disabled")) return true;
    if (el.getAttribute && el.getAttribute("aria-disabled") === "true") return true;
    if (el.hasAttribute && el.hasAttribute("disabled")) return true;
    return false;
  }

  function clickIfEnabled(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    if (isDisabled(el)) return false;
    el.click();
    return true;
  }

  function goNextOrPrepare() {
    // Wenn "Nächste" existiert -> dahin, sonst "Vorbereiten"
    if (clickIfEnabled("btnNextRound")) return true;
    return clickIfEnabled("btnPrepareNext");
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (isTypingContext()) return;

    const key = String(ev.key || "");
    const k = key.toLowerCase();

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

    // R = aktuelle Runde auslosen/neu auslosen
    // Shift+R = nächste Runde auslosen (falls Button vorhanden), sonst aktuelle Runde
    if (k === "r") {
      ev.preventDefault();
      if (ev.shiftKey) {
        // Neu: wenn "nächste Runde auslosen" nicht vorhanden/disabled -> aktuelle auslosen
        if (!clickIfEnabled("btnDrawNext")) {
          clickIfEnabled("btnRedraw");
        }
      } else {
        clickIfEnabled("btnRedraw"); // confirm hängt am formDraw
      }
      return;
    }

    // L = letzte Runde
    if (k === "l") {
      ev.preventDefault();
      clickIfEnabled("btnLastRound");
      return;
    }
  });
})();