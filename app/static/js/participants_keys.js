// app/static/js/participants_keys.js
(function () {
  let selectedIdx = -1;        // Treffer (Adressbuch)
  let selectedPartIdx = -1;    // Turnierteilnehmer

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

  function isTypingContext() {
    const tag = activeTag();
    return tag === "input" || tag === "textarea" || tag === "select";
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

  // ---------------------------------------------------------------------------
  // Treffer (Adressbuch)
  // ---------------------------------------------------------------------------
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

  function toast(msg) {
    if (window.SKT && typeof window.SKT.toast === "function") {
      window.SKT.toast(msg);
      return;
    }
    alert(msg);
  }

  function isHitDisabledRow(row) {
    if (!row) return false;
    if (row.classList.contains("sk-hit-disabled")) return true;

    // Fallback: wenn data-add-url fehlt, ist Übernehmen nicht möglich
    const url = row.dataset ? row.dataset.addUrl : "";
    return !url;
  }

  function disabledReasonMessage(row) {
    const r = row?.dataset?.disabledReason;
    if (r && String(r).trim()) return String(r);

    // Fallback, falls Template kein reason setzt
    if (Number(window.__SKT_TOURNAMENT_CLOSED__ || 0) === 1) {
      return "Turnier ist abgeschlossen – Übernehmen ist gesperrt.";
    }
    return "Maximale Teilnehmerzahl erreicht – Übernehmen nicht möglich.";
  }

  function takeSelected() {
    const rows = qsa("#hitsBody tr.sk-hit");
    if (!rows.length) return;
    if (selectedIdx < 0 || selectedIdx >= rows.length) return;

    const row = rows[selectedIdx];

    if (isHitDisabledRow(row)) {
      toast(disabledReasonMessage(row));
      return;
    }

    const url = row.dataset.addUrl;
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

        if (isHitDisabledRow(row)) {
          toast(disabledReasonMessage(row));
          return;
        }

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

  // --- Helfer: Buttons/Formulare über Tastatur auslösen ---

  function submitCheckNumbers() {
    const btn = qs("form[action*='check-numbers'] button");
    if (btn) btn.click();
  }

  function focusRenumberFrom() {
    const input = qs("form[action*='renumber-from'] input[name='start_no']");
    if (input) {
      input.focus();
      input.select?.();
    }
  }

  function submitRenumberFromIfFocused(ev) {
    const el = document.activeElement;
    if (!el) return false;

    if (el.tagName && el.tagName.toLowerCase() === "input" && el.name === "start_no") {
      const v = String(el.value || "").trim();
      if (!v) return false;

      ev.preventDefault();
      const form = el.form;
      if (form) form.submit();
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // Turnierteilnehmer: Auswahl + Shift+Entf (Entfernen ohne Renummerierung)
  // ---------------------------------------------------------------------------
  function partRows() {
    return qsa("#participantsBody tr.sk-part");
  }

  function setPartSelection(i) {
    const r = partRows();
    if (!r.length) return;

    if (i < 0) i = 0;
    if (i >= r.length) i = r.length - 1;

    r.forEach(x => x.classList.remove("sk-selected"));
    r[i].classList.add("sk-selected");
    selectedPartIdx = i;

    r[i].scrollIntoView({ block: "nearest" });
  }

  function movePartSelection(delta) {
    const r = partRows();
    if (!r.length) return;

    if (selectedPartIdx === -1) {
      setPartSelection(0);
      return;
    }
    setPartSelection(selectedPartIdx + delta);
  }

  function wireParticipantSelection() {
    const r = partRows();
    r.forEach((row, i) => {
      row.addEventListener("mouseenter", () => setPartSelection(i));
      row.addEventListener("click", (ev) => {
        const tag = (ev.target && ev.target.tagName) ? ev.target.tagName.toLowerCase() : "";
        if (tag === "button" || tag === "input" || tag === "a" || tag === "select" || tag === "textarea") return;
        setPartSelection(i);
      });
    });
  }

  function initDefaultParticipantSelection() {
    const r = partRows();
    if (r.length) setPartSelection(0);
  }

  function deleteSelectedParticipant() {
    const r = partRows();
    if (!r.length) return;
    if (selectedPartIdx < 0 || selectedPartIdx >= r.length) return;

    const row = r[selectedPartIdx];
    const formId = row.dataset.deleteFormId;
    if (!formId) return;

    const form = document.getElementById(formId);
    if (!form) return;

    const btn = form.querySelector("button[type='submit']");
    if (btn) btn.click();
  }

  function restoreSelectionFromHash() {
    const hash = String(location.hash || "").trim();
    if (!hash || hash.length < 2) return false;

    const el = document.getElementById(hash.slice(1));
    if (!el) return false;

    const rows = partRows();
    if (!rows.length) return false;

    const idx = rows.findIndex(r => r === el);
    if (idx < 0) return false;

    setPartSelection(idx);
    return true;
  }

  function onKeyDown(ev) {
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

    if (ev.altKey && (ev.key === "n" || ev.key === "N")) {
      ev.preventDefault();
      openQuickAdd();
      return;
    }

    if (ev.altKey && (ev.key === "p" || ev.key === "P")) {
      ev.preventDefault();
      submitCheckNumbers();
      return;
    }

    if (ev.altKey && (ev.key === "r" || ev.key === "R")) {
      ev.preventDefault();
      focusRenumberFrom();
      return;
    }

    if (ev.key === "Enter") {
      if (submitRenumberFromIfFocused(ev)) return;
    }

    if (ev.shiftKey && String(ev.key || "").toLowerCase() === "delete") {
      if (isTypingContext()) return;
      ev.preventDefault();
      deleteSelectedParticipant();
      return;
    }

    const tag = activeTag();
    const id = activeId();
    const inInput = (tag === "input" || tag === "textarea" || tag === "select");
    const inSearch = (id === "qInput");

    if (ev.key === "ArrowDown") {
      if (inSearch) {
        const rows = qsa("#hitsBody tr.sk-hit");
        if (rows.length) {
          ev.preventDefault();
          moveSelection(+1);
        }
        return;
      }

      if (!inInput) {
        const r = partRows();
        if (r.length) {
          ev.preventDefault();
          movePartSelection(+1);
        }
      }
      return;
    }

    if (ev.key === "ArrowUp") {
      if (inSearch) {
        const rows = qsa("#hitsBody tr.sk-hit");
        if (rows.length) {
          ev.preventDefault();
          moveSelection(-1);
        }
        return;
      }

      if (!inInput) {
        const r = partRows();
        if (r.length) {
          ev.preventDefault();
          movePartSelection(-1);
        }
      }
      return;
    }

    if (ev.key === "Enter") {
      if (inInput) return;
      const rows = qsa("#hitsBody tr.sk-hit");
      if (!rows.length) return;
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

    wireParticipantSelection();

    const restored = restoreSelectionFromHash();
    if (!restored) {
      initDefaultParticipantSelection();
    }

    document.addEventListener("keydown", onKeyDown);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();