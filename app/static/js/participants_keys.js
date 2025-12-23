// app/static/js/participants_keys.js
(function () {
  let selectedIdx = -1;

  function qs(sel, root = document) { return root.querySelector(sel); }
  function qsa(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  function activeTag() {
    return (document.activeElement && document.activeElement.tagName)
      ? document.activeElement.tagName.toLowerCase()
      : "";
  }

  function activeId() {
    return (document.activeElement && document.activeElement.id)
      ? String(document.activeElement.id)
      : "";
  }

  function isQuickAddOpen() {
    const card = qs("#quickAddCard");
    if (!card) return false;
    return card.style.display !== "none";
  }

  function openQuickAdd() {
    const card = qs("#quickAddCard");
    if (!card) return;
    card.style.display = "";
    const ln = qs("#qaNachname");
    if (ln) ln.focus();
  }

  function closeQuickAdd() {
    const card = qs("#quickAddCard");
    if (!card) return;
    card.style.display = "none";
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
    const rows = qsa("#hitsBody tr.sk-hit");
    if (rows.length) setSelection(0);
  }

  // --- Neue Helfer: Buttons/Formulare über Tastatur auslösen ---

  function submitCheckNumbers() {
    // Erstes Formular im Block "Teilnehmernummern": Prüfen
    const btn = qs("form[action*='check-numbers'] button");
    if (btn) btn.click();
  }

  function focusRenumberFrom() {
    // Input heißt start_no im Renumber-From-Formular
    const input = qs("form[action*='renumber-from'] input[name='start_no']");
    if (input) {
      input.focus();
      input.select?.();
    }
  }

  function submitRenumberFromIfFocused(ev) {
    // Wenn Fokus im start_no-Feld und Enter -> Form absenden
    const el = document.activeElement;
    if (!el) return false;
    if (el.tagName && el.tagName.toLowerCase() === "input" && el.name === "start_no") {
      // Wenn leer, nicht absenden (sonst kommt Flash-Error)
      const v = String(el.value || "").trim();
      if (!v) return false;

      ev.preventDefault();
      const form = el.form;
      if (form) form.submit();
      return true;
    }
    return false;
  }

  function onKeyDown(ev) {
    // Quick-Add offen: nur Esc / Alt+N (schließen) behandeln
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

    // Globale Shortcuts (wenn Quick-Add zu)
    if (ev.altKey && (ev.key === "n" || ev.key === "N")) {
      ev.preventDefault();
      openQuickAdd();
      return;
    }

    // Alt+P = Prüfen
    if (ev.altKey && (ev.key === "p" || ev.key === "P")) {
      ev.preventDefault();
      submitCheckNumbers();
      return;
    }

    // Alt+R = Fokus ins "Ab Nr." Feld
    if (ev.altKey && (ev.key === "r" || ev.key === "R")) {
      ev.preventDefault();
      focusRenumberFrom();
      return;
    }

    // Enter im start_no-Feld -> Renummerieren absenden
    if (ev.key === "Enter") {
      if (submitRenumberFromIfFocused(ev)) return;
    }

    // Tastatursteuerung Treffer:
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;

    // Pfeile: erlaubt, wenn Fokus NICHT in Eingabefeld ist
    // Ausnahme: im Suchfeld (#qInput) erlauben wir es ausdrücklich
    const tag = activeTag();
    const id = activeId();
    const inInput = (tag === "input" || tag === "textarea" || tag === "select");

    const allowNav =
      !inInput || id === "qInput"; // nur Suchfeld darf nav trotz input-fokus

    if (ev.key === "ArrowDown" && allowNav) {
      ev.preventDefault();
      moveSelection(+1);
      return;
    }
    if (ev.key === "ArrowUp" && allowNav) {
      ev.preventDefault();
      moveSelection(-1);
      return;
    }

    if (ev.key === "Enter") {
      // Enter übernimmt nur, wenn Fokus NICHT in einem Eingabefeld/Textarea/Select ist
      if (inInput) return;

      ev.preventDefault();
      takeSelected();
      return;
    }

    if (ev.key === "Escape") {
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