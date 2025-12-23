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

  function clickIf(id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (isDisabled(el)) return;
    el.click();
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (isTypingContext()) return;

    const key = String(ev.key || "");
    const k = key.toLowerCase();

    // Pfeilnavigation
    if (key === "ArrowLeft") {
      ev.preventDefault();
      clickIf("btnPrevRound");
      return;
    }
    if (key === "ArrowRight") {
      ev.preventDefault();
      clickIf("btnNextRound");
      return;
    }

    // Esc = zurück zum Turnier
    if (key === "Escape") {
      ev.preventDefault();
      clickIf("btnBackTournament");
      return;
    }

    // R = neu auslosen (confirm hängt am form/onsubmit)
    if (k === "r") {
      ev.preventDefault();
      clickIf("btnRedraw");
      return;
    }

    // T = Turnier
    if (k === "t") {
      ev.preventDefault();
      clickIf("btnBackTournament");
      return;
    }

    // P/N = Vor/Zurück
    if (k === "p") {
      ev.preventDefault();
      clickIf("btnPrevRound");
      return;
    }
    if (k === "n") {
      ev.preventDefault();
      clickIf("btnNextRound");
      return;
    }

    // L = letzte Runde
    if (k === "l") {
      const el = document.getElementById("btnLastRound");
      if (el && !isDisabled(el)) {
        ev.preventDefault();
        el.click();
      }
    }
  });
})();