(function () {
  const form = document.getElementById("csvImport");
  const file = document.getElementById("csvFile");
  const summary = document.getElementById("summary");
  const area = document.getElementById("confirmArea");
  const token = form.elements.preview_token;
  const previewButton = document.getElementById("previewButton");
  let revision = 0;

  function clearPreview() {
    token.value = "";
    area.hidden = true;
    summary.replaceChildren();
  }

  function message(text) {
    const line = document.createElement("p");
    line.textContent = text;
    summary.appendChild(line);
  }

  // Bei einer serverseitig angezeigten Vorschau bleibt die normale Dateiauswahl verfügbar.
  if (token.value) return;
  file.addEventListener("change", () => {
    revision++;
    clearPreview();
    previewButton.disabled = false;
  });

  form.addEventListener("submit", async event => {
    if (event.submitter?.value === "apply") return;
    event.preventDefault();
    const current = ++revision;
    clearPreview();
    previewButton.disabled = true;
    const body = new FormData(form);
    body.set("step", "preview");
    try {
      const response = await fetch(form.action, {method: "POST", body, headers: {Accept: "application/json"}});
      const result = await response.json();
      if (revision !== current) return;
      if (!response.ok) {
        message(result.error || "Die Datei konnte nicht geprüft werden.");
        return;
      }
      document.getElementById("bookCount").textContent = "Standardadressbuch · " + result.existing + " vorhandene Adressen";
      document.getElementById("bookLock").hidden = !result.registered;
      message("Gültige Adresszeilen: " + result.valid + " · Fehlerhafte Zeilen: " + result.invalid);
      result.errors.forEach(message);
      if (result.error_count > result.errors.length) message("Weitere Meldungen: " + (result.error_count - result.errors.length));
      token.value = result.token;
      area.hidden = !result.token;
    } catch {
      if (revision === current) message("Die Vorschau konnte nicht geladen werden. Bitte erneut prüfen.");
    } finally {
      if (revision === current) previewButton.disabled = false;
    }
  });
})();
