// app/static/js/round_keys.js
(function () {
  function isTypingContext() {
    const el = document.activeElement;
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  function clickIf(id) {
    const el = document.getElementById(id);
    if (!el) return;
    if (el.classList.contains("disabled")) return;
    if (el.getAttribute("aria-disabled") === "true") return;
    if (el.hasAttribute("disabled")) return;
    el.click();
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (isTypingContext()) return;

    const k = String(ev.key || "").toLowerCase();

    if (ev.key === "ArrowLeft") {
      ev.preventDefault();
      clickIf("btnPrevRound");
      return;
    }
    if (ev.key === "ArrowRight") {
      ev.preventDefault();
      clickIf("btnNextRound");
      return;
    }
    if (ev.key === "Escape") {
      ev.preventDefault();
      clickIf("btnBackToTournament");
      return;
    }

    // R = neu auslosen (confirm hängt am form/onsubmit)
    if (k === "r") {
      ev.preventDefault();
      clickIf("btnRedrawRound");
      return;
    }

    // L = letzte Runde
    if (k === "l") {
      const el = document.getElementById("btnLastRound");
      if (el) {
        ev.preventDefault();
        el.click();
      }
    }
  });
})();