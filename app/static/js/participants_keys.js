// Teilnehmerliste und Adressbuchsuche haben getrennte Auswahlen.
(function () {
  const byId = id => document.getElementById(id);
  const search = byId("qInput");
  const quickAdd = byId("quickAddCard");
  const hits = Array.from(document.querySelectorAll("#hitsBody .sk-hit"));
  const players = Array.from(document.querySelectorAll("#participantsBody .sk-part"));
  const query = byId("rosterQuery");
  const status = byId("rosterStatus");
  let hit = null;
  let player = null;
  let hitChosenByKey = false;

  function select(previous, row, scroll) {
    previous?.classList.remove("sk-selected");
    row?.classList.add("sk-selected");
    if (scroll && row) row.scrollIntoView({ block: "nearest" });
    return row;
  }

  function move(rows, selected, step) {
    const index = rows.indexOf(selected);
    return rows[Math.max(0, Math.min(rows.length - 1, index < 0 ? 0 : index + step))];
  }

  function closeQuickAdd() {
    quickAdd.open = false;
    quickAdd.querySelector("summary").focus();
  }

  quickAdd?.addEventListener("toggle", () => {
    if (quickAdd.open) byId("qaNachname").focus();
  });
  byId("btnCloseQuickAdd")?.addEventListener("click", closeQuickAdd);

  function take(row) {
    const button = row?.querySelector("form button");
    if (!button) return;
    if (button.disabled) {
      window.SKT?.toast?.(row.dataset.disabledReason);
      return;
    }
    button.click();
  }

  for (const row of hits) {
    row.addEventListener("mouseenter", () => { hit = select(hit, row, false); });
    row.addEventListener("click", event => {
      if (!event.target.closest("a, button, input")) take(row);
    });
  }
  if (hits.length) hit = select(null, hits[0], false);
  search?.addEventListener("input", () => { hitChosenByKey = false; });
  search?.addEventListener("keydown", event => {
    if (!hits.length || search.value !== search.defaultValue) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      hit = select(hit, move(hits, hitChosenByKey ? hit : null, event.key === "ArrowDown" ? 1 : -1), true);
      hitChosenByKey = true;
    } else if (event.key === "Enter" && hitChosenByKey) {
      event.preventDefault();
      take(hit);
    } else if (event.key === "Escape") {
      hitChosenByKey = false;
      hit = select(hit, null, false);
    }
  });

  function filterPlayers() {
    const terms = query.value.trim().toLocaleLowerCase("de").split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const row of players) {
      const person = row.dataset.person.toLocaleLowerCase("de");
      const matches = terms.every(term => /^#?\d+$/.test(term)
        ? row.dataset.number === term.replace(/^#/, "") : person.includes(term));
      row.hidden = !matches || (status.value !== "all" && status.value !== row.dataset.status);
      if (!row.hidden) shown++;
    }
    if (player?.hidden) player = select(player, null, false);
    byId("rosterEmpty").hidden = shown !== 0;
    byId("rosterShown").textContent = shown + " von " + players.length + " Teilnehmern sichtbar.";
  }

  if (query && status) {
    byId("rosterFilters").hidden = false;
    query.addEventListener("input", filterPlayers);
    status.addEventListener("change", filterPlayers);
    filterPlayers();
  }

  for (const row of players) {
    row.addEventListener("mouseenter", () => { player = select(player, row, false); });
    row.addEventListener("focusin", () => { player = select(player, row, false); });
    row.addEventListener("click", () => { player = select(player, row, false); });
  }
  const initial = document.getElementById(location.hash.slice(1));
  if (players.includes(initial)) player = select(null, initial, true);

  document.addEventListener("keydown", event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || document.querySelector(".modal.show")) return;
    const key = event.key.toLowerCase();
    if (quickAdd?.open && (key === "escape" || (event.altKey && key === "n"))) {
      event.preventDefault();
      closeQuickAdd();
      return;
    }
    if (event.altKey) {
      if (key === "n" && quickAdd) {
        event.preventDefault();
        quickAdd.open = true;
      } else if (key === "p" || key === "r") {
        const tools = byId("numberTools");
        if (!tools) return;
        event.preventDefault();
        tools.open = true;
        if (key === "p") tools.querySelector("form button").click();
        else byId("startNo").focus();
      }
      return;
    }
    if (quickAdd?.open || event.target.closest("input, textarea, select, [contenteditable=true]")) return;
    const row = event.target.closest("#participantsBody tr");
    if (!row && event.target.closest("a, button, summary, form")) return;
    if (key === "arrowdown" || key === "arrowup") {
      const visible = players.filter(item => !item.hidden);
      if (!visible.length) return;
      event.preventDefault();
      player = select(player, move(visible, row || player, key === "arrowdown" ? 1 : -1), true);
      player.querySelector("summary, a")?.focus({preventScroll:true});
    } else if (event.shiftKey && (key === "delete" || key === "backspace")) {
      const target = row || player;
      if (!target || target.hidden) return;
      const button = byId(target.dataset.deleteFormId)?.querySelector("button");
      if (button && !button.disabled) {
        event.preventDefault();
        button.click();
      }
    }
  });
})();
