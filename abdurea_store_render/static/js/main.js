/**
 * ABDUREA STORE — Main Client Interaction Script
 */

// --- 1. Theme Management (Light / Dark) ---
const ThemeManager = {
  storageKey: "abdurea_theme",
  init() {
    const saved = localStorage.getItem(this.storageKey);
    if (saved) {
      this.applyTheme(saved);
    } else {
      const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      this.applyTheme(prefersDark ? "dark" : "light");
    }
    this.updateToggleIcons();
  },
  applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(this.storageKey, theme);
    this.updateToggleIcons();
  },
  toggle() {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    this.applyTheme(next);
  },
  updateToggleIcons() {
    const theme = document.documentElement.getAttribute("data-theme") || "dark";
    document.querySelectorAll(".theme-toggle-btn").forEach(btn => {
      btn.innerHTML = theme === "dark" ? "☀️" : "🌙";
      btn.setAttribute("title", theme === "dark" ? "Switch to Light Mode" : "Switch to Dark Mode");
    });
  }
};

// --- 2. Toast Notifications ---
function showToast(msg, duration = 2500) {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = "toast-msg";
  toast.innerText = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    setTimeout(() => toast.remove(), 250);
  }, duration);
}

// --- 3. Clipboard Helper ---
function copyText(text, label = "Copied to clipboard!") {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(label);
    }).catch(() => fallbackCopy(text, label));
  } else {
    fallbackCopy(text, label);
  }
}

function fallbackCopy(text, label) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    showToast(label);
  } catch (err) {
    showToast("Error copying text");
  }
  document.body.removeChild(ta);
}

// --- 4. Storefront Interactions ---
document.addEventListener("DOMContentLoaded", () => {
  ThemeManager.init();

  // Theme toggle listeners
  document.querySelectorAll(".theme-toggle-btn").forEach(btn => {
    btn.addEventListener("click", () => ThemeManager.toggle());
  });

  // Mobile menu toggle
  const mobileMenuBtn = document.getElementById("mobile-menu-btn");
  const mobileNavMenu = document.getElementById("mobile-nav-menu");
  if (mobileMenuBtn && mobileNavMenu) {
    mobileMenuBtn.addEventListener("click", () => {
      mobileNavMenu.classList.toggle("hidden");
    });
  }

  // FAQ Accordion
  document.querySelectorAll(".faq-item").forEach(item => {
    const header = item.querySelector(".faq-header");
    if (header) {
      header.addEventListener("click", () => {
        const isActive = item.classList.contains("active");
        document.querySelectorAll(".faq-item").forEach(i => i.classList.remove("active"));
        if (!isActive) item.classList.add("active");
      });
    }
  });

  // Category Filtering (in Services list or Homepage)
  const categoryPills = document.querySelectorAll(".category-pill");
  const serviceCards = document.querySelectorAll(".service-card-item");
  const searchInput = document.getElementById("service-search-input");
  const emptyState = document.getElementById("no-services-state");

  function filterServices() {
    const activePill = document.querySelector(".category-pill.active");
    const selectedCat = activePill ? activePill.getAttribute("data-category") : "all";
    const query = (searchInput ? searchInput.value : "").trim().toLowerCase();

    let visibleCount = 0;

    serviceCards.forEach(card => {
      const cardCat = card.getAttribute("data-category") || "";
      const cardName = (card.getAttribute("data-name") || "").toLowerCase();
      const cardDesc = (card.getAttribute("data-desc") || "").toLowerCase();

      const matchesCat = (selectedCat === "all" || cardCat === selectedCat);
      const matchesSearch = (!query || cardName.includes(query) || cardDesc.includes(query));

      if (matchesCat && matchesSearch) {
        card.style.display = "flex";
        visibleCount++;
      } else {
        card.style.display = "none";
      }
    });

    if (emptyState) {
      if (visibleCount === 0) {
        emptyState.classList.remove("hidden");
      } else {
        emptyState.classList.add("hidden");
      }
    }
  }

  if (categoryPills.length > 0) {
    categoryPills.forEach(pill => {
      pill.addEventListener("click", () => {
        categoryPills.forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
        filterServices();
      });
    });
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      filterServices();
    });
  }

  // Handle Quick Order / Details Modal
  window.openServiceModal = function(serviceId) {
    fetch(`/api/services/${serviceId}`)
      .then(res => res.json())
      .then(data => {
        if (data.service) {
          renderServiceModal(data.service, data.settings);
          // Store recently viewed
          addRecentlyViewed(data.service);
        }
      })
      .catch(err => {
        console.error("Failed to load service", err);
        showToast("Unable to load service details");
      });
  };

  // Close Modals
  window.closeModal = function(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
      modal.classList.add("hidden");
    }
  };

  // Open Ethiopian Payment Modal
  window.openPaymentModal = function(serviceName = "", planDuration = "", price = "") {
    const modal = document.getElementById("payment-modal");
    if (modal) {
      const summaryText = document.getElementById("payment-order-summary");
      if (summaryText) {
        if (serviceName && planDuration && price) {
          summaryText.innerText = `${serviceName} — ${planDuration} (${price} ETB)`;
        } else {
          summaryText.innerText = "Select any service and choose plan";
        }
      }
      modal.classList.remove("hidden");
    }
  };

  // Render recently viewed on home page
  renderRecentlyViewed();
});

// --- 5. Service Modal Rendering ---
function renderServiceModal(service, settings) {
  const modal = document.getElementById("service-detail-modal");
  if (!modal) return;

  const content = document.getElementById("service-modal-content");
  if (!content) return;

  // Selected plan state
  let selectedPlan = service.plans && service.plans.length > 0 ? service.plans[0] : null;

  // Availability banner
  let statusBadge = "";
  if (service.availability === "available") {
    statusBadge = `<span class="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-400 bg-emerald-500/10 px-2.5 py-1 rounded-full"><span class="status-dot-available"></span> Available</span>`;
  } else if (service.availability === "limited") {
    statusBadge = `<span class="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-400 bg-amber-500/10 px-2.5 py-1 rounded-full"><span class="status-dot-limited"></span> Limited Availability</span>`;
  } else {
    statusBadge = `<span class="inline-flex items-center gap-1.5 text-xs font-semibold text-rose-400 bg-rose-500/10 px-2.5 py-1 rounded-full"><span class="status-dot-unavailable"></span> Currently Unavailable</span>`;
  }

  // Badges
  let customBadge = "";
  if (service.is_popular) {
    customBadge = `<span class="badge-popular text-xs font-bold px-2.5 py-1 rounded-full">🔥 POPULAR</span>`;
  } else if (service.is_recommended) {
    customBadge = `<span class="badge-recommended text-xs font-bold px-2.5 py-1 rounded-full">⭐ RECOMMENDED</span>`;
  } else if (service.is_premium) {
    customBadge = `<span class="badge-premium text-xs font-bold px-2.5 py-1 rounded-full">💎 PREMIUM</span>`;
  }

  // Features
  let featuresHtml = "";
  if (service.features_list && service.features_list.length > 0) {
    featuresHtml = `
      <div class="mt-4">
        <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">What is included</h4>
        <ul class="space-y-1.5 text-sm">
          ${service.features_list.map(f => `
            <li class="flex items-start gap-2">
              <span class="text-sky-400 font-bold">✓</span>
              <span class="text-slate-200">${f}</span>
            </li>
          `).join("")}
        </ul>
      </div>
    `;
  }

  // Plans HTML
  let plansHtml = "";
  if (service.plans && service.plans.length > 0) {
    plansHtml = `
      <div class="mt-4">
        <label class="block text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">Select Duration / Plan</label>
        <div class="grid grid-cols-2 gap-2.5" id="modal-plans-grid">
          ${service.plans.map((p, idx) => `
            <div class="plan-btn p-3 rounded-xl cursor-pointer ${idx === 0 ? "selected" : ""}" data-plan-id="${p.id}" data-duration="${p.duration}" data-price="${p.price}">
              <div class="flex items-center justify-between">
                <span class="font-semibold text-sm">${p.duration}</span>
                ${p.is_best_value ? `<span class="badge-best-value text-[10px] font-bold px-1.5 py-0.5 rounded">BEST VALUE</span>` : ""}
              </div>
              <div class="mt-1 text-base font-bold text-sky-400">
                ${p.price} <span class="text-xs font-normal text-slate-400">ETB</span>
              </div>
            </div>
          `).join("")}
        </div>
      </div>
    `;
  }

  const isUnavailable = service.availability === "unavailable";

  content.innerHTML = `
    <div class="p-6">
      <div class="flex items-start justify-between gap-4">
        <div class="flex items-center gap-3">
          <img src="${service.image_url || '/static/images/logo.svg'}" alt="${service.name}" class="w-14 h-14 rounded-2xl object-cover border border-slate-700/50 p-1 bg-slate-800/40">
          <div>
            <div class="flex items-center gap-2">
              <h3 class="text-xl font-bold text-slate-100">${service.name}</h3>
              ${customBadge}
            </div>
            <div class="flex items-center gap-2 mt-1">
              ${statusBadge}
              <span class="text-xs text-slate-400">${service.category_name || ''}</span>
            </div>
          </div>
        </div>
        <button onclick="closeModal('service-detail-modal')" class="text-slate-400 hover:text-white p-1 rounded-lg text-lg">✕</button>
      </div>

      <p class="mt-4 text-sm text-slate-300 leading-relaxed">${service.description || service.short_description || ''}</p>

      ${featuresHtml}
      ${plansHtml}

      ${service.requirements ? `
        <div class="mt-4 p-3 rounded-xl bg-slate-800/40 border border-slate-700/40 text-xs text-slate-300">
          <span class="font-bold text-sky-300">Requirements:</span> ${service.requirements}
        </div>
      ` : ''}

      ${service.important_notes ? `
        <div class="mt-2 text-xs text-slate-400">
          <span class="font-medium text-slate-300">Note:</span> ${service.important_notes}
        </div>
      ` : ''}

      <!-- CTAs -->
      <div class="mt-6 flex flex-col sm:flex-row gap-3">
        <button id="modal-order-telegram-btn" ${isUnavailable ? 'disabled' : ''} class="btn-telegram-primary flex-1 py-3 px-4 rounded-xl flex items-center justify-center gap-2 text-sm ${isUnavailable ? 'opacity-50 cursor-not-allowed' : ''}">
          <svg class="w-5 h-5" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/></svg>
          ORDER ON TELEGRAM
        </button>

        <button onclick="openPaymentModal('${service.name}', '${selectedPlan ? selectedPlan.duration : ''}', '${selectedPlan ? selectedPlan.price : ''}')" class="btn-secondary-glow py-3 px-4 rounded-xl flex items-center justify-center gap-2 text-sm text-sky-400 border border-sky-500/30">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z"/></svg>
          Pay with Telebirr / CBE
        </button>
      </div>
    </div>
  `;

  // Hook plan selection
  const planButtons = content.querySelectorAll(".plan-btn");
  function updateTelegramLink() {
    if (!selectedPlan) return;
    const baseLink = settings.telegram_order_link || "https://t.me/abdurea1";
    const msgTemplate = settings.telegram_default_message || "Hello ABDUREA STORE, I want to order {service} — {plan} ({price} ETB).";
    const orderMsg = msgTemplate
      .replace("{service}", service.name)
      .replace("{plan}", selectedPlan.duration)
      .replace("{price}", selectedPlan.price);

    const fullUrl = `${baseLink}?text=${encodeURIComponent(orderMsg)}`;
    const tgBtn = document.getElementById("modal-order-telegram-btn");
    if (tgBtn && !isUnavailable) {
      tgBtn.onclick = () => {
        // Track analytics
        fetch("/api/track-click", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event_type: "order_click",
            service_id: service.id,
            service_name: service.name,
            plan_duration: selectedPlan.duration,
            plan_price: selectedPlan.price
          })
        }).catch(() => {});

        window.open(fullUrl, "_blank");
      };
    }
  }

  planButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      planButtons.forEach(b => b.classList.remove("selected"));
      btn.classList.add("selected");
      const pId = btn.getAttribute("data-plan-id");
      selectedPlan = service.plans.find(p => p.id == pId) || selectedPlan;
      updateTelegramLink();
    });
  });

  updateTelegramLink();
  modal.classList.remove("hidden");
}

// --- 6. Recently Viewed in LocalStorage ---
function addRecentlyViewed(service) {
  try {
    let recent = JSON.parse(localStorage.getItem("abdurea_recent") || "[]");
    recent = recent.filter(r => r.id !== service.id);
    recent.unshift({
      id: service.id,
      name: service.name,
      slug: service.slug,
      image_url: service.image_url,
      min_price: service.plans && service.plans.length > 0 ? service.plans[0].price : 0
    });
    if (recent.length > 5) recent = recent.slice(0, 5);
    localStorage.setItem("abdurea_recent", JSON.stringify(recent));
    renderRecentlyViewed();
  } catch (e) {}
}

function renderRecentlyViewed() {
  const container = document.getElementById("recently-viewed-list");
  const section = document.getElementById("recently-viewed-section");
  if (!container || !section) return;

  try {
    const recent = JSON.parse(localStorage.getItem("abdurea_recent") || "[]");
    if (recent.length === 0) {
      section.classList.add("hidden");
      return;
    }
    section.classList.remove("hidden");
    container.innerHTML = recent.map(item => `
      <div onclick="openServiceModal(${item.id})" class="flex items-center gap-3 p-3 rounded-xl bg-slate-800/30 hover:bg-slate-800/60 border border-slate-700/40 cursor-pointer transition-all">
        <img src="${item.image_url || '/static/images/logo.svg'}" class="w-10 h-10 rounded-lg object-cover p-0.5 bg-slate-800">
        <div class="overflow-hidden">
          <div class="text-sm font-semibold text-slate-200 truncate">${item.name}</div>
          <div class="text-xs text-sky-400 font-medium">From ${item.min_price} ETB</div>
        </div>
      </div>
    `).join("");
  } catch (e) {}
}
