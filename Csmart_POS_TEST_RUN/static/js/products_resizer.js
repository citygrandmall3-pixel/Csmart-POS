(() => {
  const init = () => {
    const main = document.querySelector(".k-main-area");
    const handle = document.getElementById("productsResizeHandle");
    if (!main || !handle) return;
    const min = 130, max = 420, key = "csmart_products_width";
    const saved = Number(localStorage.getItem(key));
    if (saved >= min && saved <= max) main.style.setProperty("--products-width", saved + "px");
    handle.addEventListener("pointerdown", e => {
      e.preventDefault();
      handle.setPointerCapture(e.pointerId);
      const move = ev => {
        const rect = main.getBoundingClientRect();
        const width = Math.max(min, Math.min(max, Math.round(rect.right - ev.clientX)));
        main.style.setProperty("--products-width", width + "px");
        localStorage.setItem(key, String(width));
      };
      const stop = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
