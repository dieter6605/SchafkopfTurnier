// app/static/js/addresses_keys.js
(function () {
  function isTypingContext() {
    const el = document.activeElement;
    if (!el) return false;
    const tag = (el.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select";
  }

  function isDisabled(el) {
    if (!el) return true;
    if (el.classList && el.classList.contains("disabled")) return true;
    if (el.getAttribute && el.getAttribute("aria-disabled") === "true") return true;
    if (el.hasAttribute && el.hasAttribute("disabled")) return true;
    return false;
  }

  function clickIfEnabled(id) {
    const el = document.getElementById(id);
    if (!el) return false;
    if (isDisabled(el)) return false;
    el.click();
    return true;
  }

  function focusSearch() {
    const el = document.getElementById("addrSearch");
    if (!el) return false;
    try {
      el.focus();
      el.select();
    } catch (e) {}
    return true;
  }

  function clearSearchAndGoHome() {
    // Robust auf /addresses ohne Query.
    const base = "/addresses";
    if (location.pathname === base && !location.search) {
      const el = document.getElementById("addrSearch");
      if (el) el.value = "";
      return true;
    }
    location.href = base;
    return true;
  }

  // -----------------------------
  // NEU: View Toggle & Pagination
  // -----------------------------
  function currentUrl() {
    return new URL(window.location.href);
  }

  function hasPagination() {
    // Wenn page im Query steht ODER Pagination-UI vorhanden ist, behandeln wir es als paginiert
    const u = currentUrl();
    if (u.searchParams.has("page")) return true;
    const pager = document.querySelector("ul.pagination");
    return !!pager;
  }

  function getIntParam(u, key, defVal) {
    const raw = u.searchParams.get(key);
    const n = parseInt(String(raw || ""), 10);
    return isFinite(n) && n > 0 ? n : defVal;
  }

  function getTotalPagesFromDom() {
    // Wir versuchen, aus der Pagination die letzte Seitenzahl zu ermitteln.
    // (Das ist robust genug, ohne Template-IDs zu verlangen.)
    const links = Array.from(document.querySelectorAll("ul.pagination a.page-link"));
    let maxN = 0;
    for (const a of links) {
      const t = (a.textContent || "").trim();
      if (/^\d+$/.test(t)) {
        const n = parseInt(t, 10);
        if (n > maxN) maxN = n;
      }
    }
    return maxN;
  }

  function gotoPage(targetPage) {
    if (!hasPagination()) return false;

    const u = currentUrl();
    const total = getTotalPagesFromDom();
    const cur = getIntParam(u, "page", 1);

    let p = parseInt(String(targetPage), 10);
    if (!isFinite(p) || p < 1) p = 1;
    if (total > 0 && p > total) p = total;

    if (p === cur && u.searchParams.has("page")) return true;

    u.searchParams.set("page", String(p));
    window.location.href = u.toString();
    return true;
  }

  function nextPage() {
    if (!hasPagination()) return false;
    const u = currentUrl();
    const cur = getIntParam(u, "page", 1);
    return gotoPage(cur + 1);
  }

  function prevPage() {
    if (!hasPagination()) return false;
    const u = currentUrl();
    const cur = getIntParam(u, "page", 1);
    return gotoPage(cur - 1);
  }

  function firstPage() {
    return gotoPage(1);
  }

  function lastPage() {
    if (!hasPagination()) return false;
    const total = getTotalPagesFromDom();
    if (total <= 0) return false;
    return gotoPage(total);
  }

  function toggleViewAllLatest() {
    // A: view toggeln (latest <-> all)
    const u = currentUrl();
    const cur = (u.searchParams.get("view") || "latest").toLowerCase();
    const next = cur === "all" ? "latest" : "all";
    u.searchParams.set("view", next);

    // Beim Umschalten: Seite zurücksetzen (verhindert "leere Seite")
    u.searchParams.set("page", "1");

    window.location.href = u.toString();
    return true;
  }

  // -----------------------------
  // Key handling
  // -----------------------------
  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;

    const key = String(ev.key || "");
    const k = key.toLowerCase();

    // "/" fokussiert Suche (auch wenn man gerade nicht tippt)
    if (key === "/") {
      if (isTypingContext()) return;
      ev.preventDefault();
      focusSearch();
      return;
    }

    // ESC: zurück zur Adressbuch-Startansicht (ohne Query)
    if (key === "Escape") {
      if (isTypingContext()) return;
      ev.preventDefault();
      clearSearchAndGoHome();
      return;
    }

    // Rest nur außerhalb von Eingabefeldern
    if (isTypingContext()) return;

    // N = Neue Adresse
    if (k === "n") {
      ev.preventDefault();
      clickIfEnabled("btnAddrNew");
      return;
    }

    // I = Import
    if (k === "i") {
      ev.preventDefault();
      clickIfEnabled("btnAddrImport");
      return;
    }

    // X = Export
    if (k === "x") {
      ev.preventDefault();
      clickIfEnabled("btnAddrExport");
      return;
    }

    // L = Liste (zurück)
    if (k === "l") {
      ev.preventDefault();
      clickIfEnabled("btnAddrBack");
      return;
    }

    // A = Ansicht umschalten (latest <-> all)
    if (k === "a") {
      ev.preventDefault();
      toggleViewAllLatest();
      return;
    }

    // Pagination: ← / → (nur wenn paginiert)
    if (key === "ArrowLeft") {
      if (!hasPagination()) return;
      ev.preventDefault();
      prevPage();
      return;
    }
    if (key === "ArrowRight") {
      if (!hasPagination()) return;
      ev.preventDefault();
      nextPage();
      return;
    }

    // Pagination: Home/End
    if (key === "Home") {
      if (!hasPagination()) return;
      ev.preventDefault();
      firstPage();
      return;
    }
    if (key === "End") {
      if (!hasPagination()) return;
      ev.preventDefault();
      lastPage();
      return;
    }
  });
})();