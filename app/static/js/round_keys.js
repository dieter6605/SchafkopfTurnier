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
    if (!el || isDisabled(el)) return false;
    el.click();
    return true;
  }

  // Wenn "Nächste" nicht existiert (disabled), dann "Vorbereiten" nutzen
  function goNextOrPrepare() {
    if (clickIf("btnNextRound")) return;
    clickIf("btnPrepareNext");
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (isTypingContext()) return;

    const k = String(ev.key || "").toLowerCase();

    // Navigation per Pfeiltasten
    if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      clickIf("btnPrevRound");
      return;
    }
    if (ev.key === "ArrowRight") {
      ev.preventDefault();
      goNextOrPrepare();
      return;
    }

    // Escape -> Turnier
    if (ev.key === "Escape") {
      ev.preventDefault();
      clickIf("btnBackTournament");
      return;
    }

    // T -> Turnier
    if (k === "t") {
      ev.preventDefault();
      clickIf("btnBackTournament");
      return;
    }

    // Shift+R -> nächste Runde auslosen (confirm hängt am form/onsubmit)
    if (k === "r" && ev.shiftKey) {
      ev.preventDefault();
      clickIf("btnDrawNext");
      return;
    }

    // R -> aktuelle Runde auslosen/neu auslosen
    if (k === "r") {
      ev.preventDefault();
      clickIf("btnRedraw");
      return;
    }

    // P/N -> Vor/Zurück
    if (k === "p") {
      ev.preventDefault();
      clickIf("btnPrevRound");
      return;
    }
    if (k === "n") {
      ev.preventDefault();
      goNextOrPrepare();
      return;
    }

    // L -> letzte Runde
    if (k === "l") {
      const el = document.getElementById("btnLastRound");
      if (el && !isDisabled(el)) {
        ev.preventDefault();
        el.click();
      }
    }
  });
})();