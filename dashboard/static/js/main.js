/* ═══════════════════════════════════════════════════════════════════
   Arqon Dashboard — main.js
   Loading effects: page progress bar, skeleton shimmer, button
   spinners, toasts, count-up numbers, animated resource bars,
   ripple feedback, live clock.
   ═══════════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  /* ── Page navigation progress bar ─────────────────────────────── */
  const progress = document.createElement("div");
  progress.className = "page-progress";
  document.body.appendChild(progress);

  window.addEventListener("load", () => {
    progress.classList.add("active");
    setTimeout(() => progress.classList.add("done"), 350);
    setTimeout(() => progress.classList.remove("active", "done"), 800);
  });

  // intercept internal link clicks → show bar while navigating
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href");
    if (href && href.startsWith("/") && !href.startsWith("//") && a.target !== "_blank") {
      progress.classList.add("active");
      progress.classList.remove("done");
    }
  });

  /* ── Skeleton shimmer on first paint ──────────────────────────── */
  // Any element with [data-skeleton] gets shimmered then revealed.
  // Uses the .skeleton-pending class — the .reveal CSS animation
  // (fill-forwards) overrides inline opacity, which previously forced the
  // content visible immediately and broke the effect.
  document.querySelectorAll("[data-skeleton]").forEach((el) => {
    const delay = parseInt(el.getAttribute("data-skeleton") || "500", 10);
    el.classList.add("skeleton-pending");
    const sk = document.createElement("div");
    sk.innerHTML = `
      <div class="skeleton-row"><div class="skeleton avatar-sq"></div>
      <div style="flex:1"><div class="skeleton skeleton-line w-40"></div>
      <div class="skeleton skeleton-line w-60" style="margin-top:8px"></div></div></div>`;
    sk.style.margin = "6px 0";
    el.parentNode.insertBefore(sk, el);
    setTimeout(() => {
      sk.style.transition = "opacity .4s";
      sk.style.opacity = "0";
      setTimeout(() => sk.remove(), 420);
      el.style.transition = "opacity .45s";
      el.classList.remove("skeleton-pending");
    }, delay);
  });

  /* ── Toasts ───────────────────────────────────────────────────── */
  const toastStack = document.createElement("div");
  toastStack.className = "toast-stack";
  document.body.appendChild(toastStack);

  function toast(message, type = "info", ttl = 4200) {
    const icons = { success: "✅", error: "⛔", info: "💡" };
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    // textContent (not innerHTML) — a future flash message built from user
    // data must never become an XSS sink
    const icon = document.createElement("span");
    icon.className = "t-icon";
    icon.textContent = icons[type] || icons.info;
    const body = document.createElement("span");
    body.textContent = message;
    el.append(icon, body);
    toastStack.appendChild(el);
    setTimeout(() => {
      el.classList.add("out");
      setTimeout(() => el.remove(), 320);
    }, ttl);
  }
  window.arqonToast = toast;

  // convert server-side flashes (in .flash-data JSON) into toasts
  document.querySelectorAll(".flash-data .flash-item").forEach((item) => {
    const cat = item.getAttribute("data-cat") || "info";
    toast(item.textContent.trim(), cat === "error" ? "error" : cat === "success" ? "success" : "info");
  });

  /* ── Count-up animation for stat values ───────────────────────── */
  function countUp(el) {
    const target = parseFloat(el.getAttribute("data-count") || "0");
    const suffix = el.getAttribute("data-suffix") || "";
    const dur = 900;
    const t0 = performance.now();
    function frame(t) {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased).toLocaleString() + suffix;
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
  const counterObs = new IntersectionObserver((entries) => {
    entries.forEach((en) => {
      if (en.isIntersecting) {
        countUp(en.target);
        counterObs.unobserve(en.target);
      }
    });
  }, { threshold: 0.4 });
  document.querySelectorAll("[data-count]").forEach((el) => counterObs.observe(el));

  /* ── Animated resource bars ───────────────────────────────────── */
  function fillBars() {
    document.querySelectorAll(".res-bar > span[data-fill]").forEach((bar) => {
      const val = Math.min(100, parseFloat(bar.getAttribute("data-fill") || "0"));
      if (val >= 85) bar.classList.add("hot");
      setTimeout(() => { bar.style.width = val + "%"; }, 150);
    });
  }
  window.addEventListener("load", fillBars);

  /* ── Button ripple + loading spinner on submit ────────────────── */
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".btn");
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const ripple = document.createElement("span");
    ripple.className = "ripple";
    const size = Math.max(rect.width, rect.height);
    ripple.style.width = ripple.style.height = size + "px";
    ripple.style.left = (e.clientX - rect.left - size / 2) + "px";
    ripple.style.top = (e.clientY - rect.top - size / 2) + "px";
    btn.appendChild(ripple);
    setTimeout(() => ripple.remove(), 600);
  });

  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("button[type=submit], .btn");
      if (btn) {
        btn.classList.add("loading");
        if (!btn.querySelector(".spinner")) {
          const sp = document.createElement("span");
          sp.className = "spinner";
          btn.prepend(sp);
        }
      }
    });
  });

  /* ── Live clock in topbar ─────────────────────────────────────── */
  const clock = document.getElementById("live-clock");
  if (clock) {
    function tick() {
      clock.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }
    tick();
    setInterval(tick, 1000);
  }

  /* ── Mobile sidebar ───────────────────────────────────────────── */
  const hamburger = document.getElementById("hamburger");
  const sidebar = document.getElementById("sidebar");
  if (hamburger && sidebar) {
    hamburger.addEventListener("click", () => sidebar.classList.toggle("open"));
    document.addEventListener("click", (e) => {
      // containment check — the <i> icon inside the button is the real
      // e.target, so `e.target !== hamburger` self-cancelled every tap
      if (sidebar.classList.contains("open") && !sidebar.contains(e.target) && !hamburger.contains(e.target)) {
        sidebar.classList.remove("open");
      }
    });
  }

  /* ── Auto-refresh monitoring values (soft poll) ───────────────── */
  const monitorPanel = document.querySelector("[data-autorefresh]");
  if (monitorPanel) {
    const secs = parseInt(monitorPanel.getAttribute("data-autorefresh") || "30", 10);
    setInterval(() => {
      // soft visual cue instead of hard reload
      monitorPanel.style.transition = "opacity .3s";
      monitorPanel.style.opacity = "0.55";
      setTimeout(() => { window.location.reload(); }, 350);
    }, secs * 1000);
  }
})();
