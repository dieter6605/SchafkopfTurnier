// app/static/js/wohnort_autocomplete.js
(function () {
  const cache = new Map(); // q -> array
  let lastFetchAt = 0;

  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      if (t) clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  async function fetchWohnorte(q) {
    const key = q.toLowerCase().trim();
    if (cache.has(key)) return cache.get(key);

    // kleines Rate-Limit, damit man nicht jede Taste sofort abfeuert
    const now = Date.now();
    if (now - lastFetchAt < 120) {
      await new Promise((r) => setTimeout(r, 120));
    }
    lastFetchAt = Date.now();

    const resp = await fetch(`/api/wohnorte?q=${encodeURIComponent(q)}`, {
      headers: { "Accept": "application/json" },
    });
    if (!resp.ok) return [];
    const data = await resp.json();
    cache.set(key, data);
    return data;
  }

  function ensureDatalist(input) {
    // Wenn im HTML "list" gesetzt ist, verwenden wir das.
    // Wenn nicht, erzeugen wir eine unique datalist und hängen sie an.
    let listId = input.getAttribute("list");
    if (!listId) {
      listId = `wohnortListAuto_${Math.random().toString(36).slice(2)}`;
      input.setAttribute("list", listId);
    }

    let dl = document.getElementById(listId);
    if (!dl) {
      dl = document.createElement("datalist");
      dl.id = listId;
      document.body.appendChild(dl);
    }
    return dl;
  }

  function fillPlzOrt(input, item) {
    const plzId = input.dataset.plzId;
    const ortId = input.dataset.ortId;

    if (plzId) {
      const plzEl = document.getElementById(plzId);
      if (plzEl && !plzEl.value) plzEl.value = item.plz || "";
    }
    if (ortId) {
      const ortEl = document.getElementById(ortId);
      if (ortEl && !ortEl.value) ortEl.value = item.ort || "";
    }
  }

  async function updateDatalist(input) {
    const q = (input.value || "").trim();
    const dl = ensureDatalist(input);

    if (q.length < 2) {
      dl.innerHTML = "";
      return;
    }

    const items = await fetchWohnorte(q);
    dl.innerHTML = items
      .map((x) => `<option value="${escapeHtml(x.wohnort)}"></option>`)
      .join("");

    // falls exakt getroffen: PLZ/Ort befüllen (wenn leer)
    const exact = items.find((x) => (x.wohnort || "").toLowerCase() === q.toLowerCase());
    if (exact) fillPlzOrt(input, exact);
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  const onInput = debounce((ev) => updateDatalist(ev.target), 160);

  function wireInput(input) {
    ensureDatalist(input);

    input.addEventListener("input", onInput);

    // Bei change (z.B. Klick auf Vorschlag) PLZ/Ort versuchen zu setzen
    input.addEventListener("change", async () => {
      const q = (input.value || "").trim();
      if (q.length < 2) return;
      const items = await fetchWohnorte(q);
      const exact = items.find((x) => (x.wohnort || "").toLowerCase() === q.toLowerCase());
      if (exact) fillPlzOrt(input, exact);
    });
  }

  function init() {
    document.querySelectorAll("input.sk-wohnort-input").forEach(wireInput);

    // Für Modals: Inputs werden ggf. erst beim Öffnen gerendert → wir nehmen Event-Delegation via MutationObserver
    const mo = new MutationObserver(() => {
      document.querySelectorAll("input.sk-wohnort-input").forEach((el) => {
        if (!el.dataset.wohnortWired) {
          el.dataset.wohnortWired = "1";
          wireInput(el);
        }
      });
    });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();