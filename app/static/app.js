(function () {
  const hitsBody = document.getElementById("hitsBody");
  if (!hitsBody) return;

  let idx = -1;

  function rows() {
    return Array.from(hitsBody.querySelectorAll("tr.sk-hit"));
  }

  function setSelected(i) {
    const r = rows();
    r.forEach(x => x.classList.remove("sk-selected"));
    if (i >= 0 && i < r.length) {
      r[i].classList.add("sk-selected");
      r[i].scrollIntoView({ block: "nearest" });
      idx = i;
    } else {
      idx = -1;
    }
  }

  function submitSelected() {
    const r = rows();
    if (idx < 0 || idx >= r.length) return;
    const url = r[idx].dataset.addUrl;
    if (!url) return;

    const form = document.createElement("form");
    form.method = "POST";
    form.action = url;
    document.body.appendChild(form);
    form.submit();
  }

  document.addEventListener("keydown", (ev) => {
    const r = rows();
    if (!r.length) return;

    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      setSelected(idx < 0 ? 0 : Math.min(idx + 1, r.length - 1));
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      setSelected(idx <= 0 ? 0 : idx - 1);
    } else if (ev.key === "Enter") {
      const tag = (document.activeElement && document.activeElement.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      ev.preventDefault();
      submitSelected();
    }
  });

  if (rows().length) setSelected(0);
})();