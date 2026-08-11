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

  // header status: green = live API, amber = nightly snapshot fallback in play
  SODA.onStatus((s) => {
    const el = document.getElementById("last-updated");
    const dot = document.querySelector(".pulse");
    if (s.snapshotUsed) {
      const when = s.snapshotAt
        ? new Date(s.snapshotAt).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
        : "last nightly run";
      el.textContent = s.liveUsed
        ? `Live · some feeds from the nightly snapshot (${when})`
        : `Nightly snapshot · refreshed ${when}`;
      dot.classList.add("snap");
    } else if (s.liveUsed) {
      el.textContent = "Live · fetched " + new Date().toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
      dot.classList.remove("snap");
    }
  });

  // boot: honor a #hash deep link; council feed always starts (it feeds a KPI tile)
  const initial = (location.hash || "#overview").slice(1);
  showTab(["overview", "map", "permits", "zoning", "council"].includes(initial) ? initial : "overview");
  Views.renderCouncil();
  started.add("council-data");
})();
