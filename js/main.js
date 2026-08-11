/* ============================================================
   Groundwork ATX — boot & tab shell
   ============================================================ */

(() => {
  const panels = document.querySelectorAll(".panel");
  const tabs = document.querySelectorAll(".tab");
  const started = new Set();

  function startPanel(name) {
    if (started.has(name)) {
      if (name === "map") MapView.onShow(); // re-measure even when already started
      return;
    }
    started.add(name);
    switch (name) {
      case "overview": Views.renderOverview(); break;
      case "map": MapView.onShow(); break;
      case "permits": Views.wirePermitFilters(); Views.loadPermitFeed(); break;
      case "zoning": Views.wireZoningFilters(); Views.renderZoning(); break;
      case "council": Views.wireCouncilFilters(); break; // feed itself starts at boot for the KPI tile
    }
  }

  function showTab(name) {
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on);
    });
    panels.forEach((p) => {
      const on = p.id === "panel-" + name;
      p.classList.toggle("active", on);
      p.hidden = !on;
    });
    startPanel(name);
    if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  }

  tabs.forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

  // in-content shortcuts like the lifecycle strip's links
  document.querySelectorAll("[data-goto]").forEach((a) =>
    a.addEventListener("click", (e) => { e.preventDefault(); showTab(a.dataset.goto); }));

  document.getElementById("refresh-btn").addEventListener("click", () => {
    try {
      Object.keys(sessionStorage)
        .filter((k) => k.startsWith("gw:"))
        .forEach((k) => sessionStorage.removeItem(k));
    } catch { /* ignore */ }
    location.reload();
  });

  // stamp the header once any fetch has succeeded
  const stamp = () => {
    const el = document.getElementById("last-updated");
    el.textContent = "Live · fetched " + new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  };
  const origFetch = window.fetch;
  let stamped = false;
  window.fetch = async (...args) => {
    const res = await origFetch(...args);
    if (!stamped && res.ok && String(args[0]).includes(GW_CONFIG.portal)) {
      stamped = true;
      stamp();
    }
    return res;
  };

  // boot: honor a #hash deep link; council feed always starts (it feeds a KPI tile)
  const initial = (location.hash || "#overview").slice(1);
  showTab(["overview", "map", "permits", "zoning", "council"].includes(initial) ? initial : "overview");
  Views.renderCouncil();
  started.add("council-data");
})();
