// kytary.cz boutique guitars: the shared watchlist plus a full-listing refresh.

const list = createWatchlist({
  api: "/api/kytary-boutique",
  siteName: "kytary.cz",
  emptyText: "No ads yet. Use Check for new ads to load the boutique listing.",
  describe: (ad) => ({
    name: ad.name,
    lead: ad.brand,
    rest: ad.name.startsWith(ad.brand) ? ad.name.slice(ad.brand.length).trim() : ad.name,
    meta: [ad.type, ad.id],
    avail: ad.availability,
  }),
});

const refreshBtn = document.getElementById("refresh");
refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "Checking kytary.cz…";
  try {
    const r = await api("/api/kytary-boutique/refresh", { method: "POST" });
    await list.load();
    showToast(`Found ${r.found} ads: ${r.new_ads} new, ${r.gone_ads} gone.`);
  } catch (e) {
    showToast(`Check failed: ${e.message}`, null, true);
  } finally {
    refreshBtn.disabled = false;
    refreshBtn.textContent = "Check for new ads";
  }
});

list.load().catch((e) => showToast(`Could not load ads: ${e.message}`, null, true));
