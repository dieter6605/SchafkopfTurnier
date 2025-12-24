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
    // Wenn wir in der Trefferansicht sind, ist meistens ?q=... gesetzt.
    // Wir gehen robust auf /addresses ohne Query.
    const base = "/addresses";
    if (location.pathname === base && !location.search) {
      // schon "home" -> nur ggf. Suchfeld leeren
      const el = document.getElementById("addrSearch");
      if (el) el.value = "";
      return true;
    }
    location.href = base;
    return true;
  }

  document.addEventListener("keydown", function (ev) {
    if (ev.altKey || ev.ctrlKey || ev.metaKey) return;

    const key = String(ev.key || "");
    const k = key.toLowerCase();

    // "/" fokussiert Suche (auch wenn man gerade nicht tippt)
    if (key === "/") {
      // Wenn man bereits in einem Input ist, nicht stören
      if (isTypingContext()) return;
      ev.preventDefault();
      focusSearch();
      return;
    }

    // ESC: zurück zur Adressbuch-Startansicht (ohne Suche)
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
  });
})();