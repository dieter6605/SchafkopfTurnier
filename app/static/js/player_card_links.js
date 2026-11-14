// Die aktuelle Auswahl gehört zur Liste und zu ihren Kartenlinks, auch in einem neuen Tab.
(() => {
  const fields = document.querySelectorAll("[data-card-filter]");
  const links = document.querySelectorAll("a[data-player-card]");

  function withFilters(href) {
    const url = new URL(href);
    for (const field of fields) {
      const key = field.dataset.cardFilter;
      if (field.value && !(key === "status" && field.value === "all")) {
        url.searchParams.set(key, field.value);
      } else {
        url.searchParams.delete(key);
      }
    }
    return url.href;
  }

  function updateLinks() {
    if (!fields.length) return;
    const list = withFilters(location.href);
    if (list !== location.href) history.replaceState(history.state, "", list);
    for (const link of links) {
      const href = withFilters(link.href);
      if (href !== link.href) link.href = href;
    }
  }

  for (const field of fields) {
    field.addEventListener(field.tagName === "SELECT" ? "change" : "input", updateLinks);
  }
  window.addEventListener("pageshow", updateLinks);
})();
