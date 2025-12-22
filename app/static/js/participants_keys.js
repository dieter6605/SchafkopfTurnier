// app/static/js/participants_keys.js
(function () {
  let selectedIdx = -1;

  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  function isQuickAddOpen() {
    const card = qs("#quickAddCard");
    if (!card) return false;
    return card.style.display !== "none";
  }

  function openQuickAdd() {
    const card = qs("#quickAddCard");
    if (!card) return;
    card.style.display = "";
    // Fokus auf Nachname
    const ln = qs("#qaNachname");
    if (ln) ln.focus();
  }

  function closeQuickAdd() {
    const card = qs("#quickAddCard");
    if (!card) return;
    card.style.display = "none";
    // Fokus zurück auf Suche
    const q = qs("#qInput");
    if (q) q.focus();
  }

  function clearSelection() {
    const rows = qsa("#hitsBody tr.sk-hit");
    rows.forEach(r => r.classList.remove("sk-selected"));
    selectedIdx = -1;
  }

  function setSelection(idx) {
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;

    if (idx < 0) idx = 0;
    if (idx >= rows.length) idx = rows.length - 1;

    rows.forEach(r => r.classList.remove("sk-selected"));
    rows[idx].classList.add("sk-selected");
    selectedIdx = idx;

    // Sichtbar scrollen
    rows[idx].scrollIntoView({ block: "nearest" });
  }

  function moveSelection(delta) {
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;

    if (selectedIdx === -1) {
      setSelection(0);
      return;
    }
    setSelection(selectedIdx + delta);
  }

  function takeSelected() {
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;
    if (selectedIdx < 0 || selectedIdx >= rows.length) return;

    const url = rows[selectedIdx].dataset.addUrl;
    if (!url) return;

    // POST per verstecktem Formular (sauberer als window.location bei POST-Routen)
    const form = document.createElement("form");
    form.method = "POST";
    form.action = url;
    document.body.appendChild(form);
    form.submit();
  }

  function wireMouseSelection() {
    const rows = qsa("#hitsBody tr.sk-hit");
    rows.forEach((row, i) => {
      row.addEventListener("mouseenter", () => setSelection(i));
      row.addEventListener("click", (ev) => {
        // Klick irgendwo in der Zeile: übernehmen
        // Aber wenn man direkt den Button klickt, macht das das innere Formular ohnehin.
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : "";
        if (tag === "button" || tag === "input" || tag === "a") return;
        setSelection(i);
        takeSelected();
      });
    });
  }

  function wireQuickAddButtons() {
    const btnOpen = qs("#btnOpenQuickAdd");
    const btnClose = qs("#btnCloseQuickAdd");

    if (btnOpen) btnOpen.addEventListener("click", openQuickAdd);
    if (btnClose) btnClose.addEventListener("click", closeQuickAdd);
  }

  function initDefaultSelection() {
    // Wenn Treffer da sind, standardmäßig den ersten markieren
    const rows = qsa("#hitsBody tr.sk-hit");
    if (rows.length) setSelection(0);
  }

  function onKeyDown(ev) {
    // Wenn Quick-Add offen ist:
    // - Esc schließt
    // - Alt+N toggelt (optional)
    // - Sonst keine Treffersteuerung, damit man normal tippen kann
    if (isQuickAddOpen()) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        closeQuickAdd();
      } else if (ev.altKey && (ev.key === "n" || ev.key === "N")) {
        ev.preventDefault();
        closeQuickAdd();
      }
      return;
    }

    // Quick-Add öffnen
    if (ev.altKey && (ev.key === "n" || ev.key === "N")) {
      ev.preventDefault();
      openQuickAdd();
      return;
    }

    // Tastatursteuerung nur, wenn Treffer-Tabelle existiert
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;

    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      moveSelection(+1);
      return;
    }
    if (ev.key === "ArrowUp") {
      ev.preventDefault();
      moveSelection(-1);
      return;
    }
    if (ev.key === "Enter") {
      // Nur Enter übernehmen, wenn Fokus NICHT in einem Eingabefeld/Textarea/Select ist
      const tag = (document.activeElement && document.activeElement.tagName)
        ? document.activeElement.tagName.toLowerCase()
        : "";
      if (tag === "input" || tag === "textarea" || tag === "select") return;

      ev.preventDefault();
      takeSelected();
      return;
    }
    if (ev.key === "Escape") {
      // Escape löscht Markierung
      clearSelection();
      return;
    }
  }

  function init() {
    wireQuickAddButtons();
    wireMouseSelection();
    initDefaultSelection();
    document.addEventListener("keydown", onKeyDown);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();