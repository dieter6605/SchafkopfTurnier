(() => {
  const filter = document.getElementById("standingsFilter");
  if (!filter) return;

  const query = document.getElementById("standingsQuery");
  const matches = document.getElementById("standingsMatches");
  const rows = Array.from(document.querySelectorAll("#standingsTable tbody tr"));

  function filterPlayers() {
    const terms = query.value.trim().toLocaleLowerCase("de").split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const row of rows) {
      const person = row.dataset.person.toLocaleLowerCase("de");
      const found = terms.every(term => /^#?\d+$/.test(term)
        ? row.dataset.number === term.replace(/^#/, "")
        : person.includes(term));
      row.classList.toggle("sk-filtered-out", !found);
      if (found) visible++;
    }
    matches.textContent = !terms.length ? "" : visible ? `${visible} von ${rows.length} Teilnehmern` : "Keine passenden Teilnehmer.";
  }

  filter.hidden = false;
  query.addEventListener("input", filterPlayers);
  filterPlayers();
})();
