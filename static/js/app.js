function showMessage(element, type, text) {
  if (!element) {
    return;
  }

  element.hidden = false;
  element.className = `message-box is-${type}`;
  element.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderBadges(establishment) {
  const badges = [];
  if (establishment.accepts_lightning) {
    badges.push('<span class="payment-badge is-lightning">Lightning</span>');
  }
  if (establishment.accepts_onchain) {
    badges.push('<span class="payment-badge is-onchain">On-chain</span>');
  }
  if (establishment.accepts_contactless) {
    badges.push('<span class="payment-badge is-contactless">Contactless</span>');
  }
  return badges.join("");
}

function establishmentCardTemplate(establishment) {
  const website = establishment.website
    ? `<a href="${escapeHtml(establishment.website)}" target="_blank" rel="noopener" class="inline-link">Abrir site</a>`
    : "";

  return `
    <article class="establishment-card">
      <div class="establishment-logo-wrap">
        <img src="${escapeHtml(establishment.logo_url)}" alt="${escapeHtml(establishment.name)}" class="establishment-logo">
      </div>
      <div class="establishment-body">
        <span class="pill">${escapeHtml(establishment.type_label)}</span>
        <h3>${escapeHtml(establishment.name)}</h3>
        <p>${escapeHtml(establishment.address)}</p>
        <div class="meta-row">
          <span><i class="fa-solid fa-location-dot"></i> ${escapeHtml(establishment.city_label)}</span>
        </div>
        <div class="badge-row">${renderBadges(establishment)}</div>
        ${website}
      </div>
    </article>
  `;
}

function initMobileMenu() {
  const toggle = document.getElementById("menu-toggle");
  const menu = document.getElementById("mobile-nav");

  if (!toggle || !menu) {
    return;
  }

  toggle.addEventListener("click", () => {
    const isOpen = !menu.hasAttribute("hidden");
    if (isOpen) {
      menu.setAttribute("hidden", "");
      toggle.setAttribute("aria-expanded", "false");
    } else {
      menu.removeAttribute("hidden");
      toggle.setAttribute("aria-expanded", "true");
    }
  });
}

function initTabs() {
  document.querySelectorAll("[data-tabs]").forEach((tabsRoot) => {
    const triggers = tabsRoot.querySelectorAll("[data-tab-trigger]");
    const panels = tabsRoot.querySelectorAll("[data-tab-panel]");

    triggers.forEach((trigger) => {
      trigger.addEventListener("click", () => {
        const key = trigger.dataset.tabTrigger;

        triggers.forEach((item) => item.classList.remove("is-active"));
        panels.forEach((panel) => panel.classList.remove("is-active"));

        trigger.classList.add("is-active");
        tabsRoot.querySelector(`[data-tab-panel="${key}"]`)?.classList.add("is-active");
      });
    });
  });
}

function initCopyButtons() {
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const targetId = button.dataset.copyTarget;
      const target = document.getElementById(targetId);
      if (!target) {
        return;
      }

      try {
        await navigator.clipboard.writeText(target.innerText.trim());
        button.textContent = "Copiado";
        window.setTimeout(() => {
          button.textContent = "Copiar";
        }, 1500);
      } catch (error) {
        button.textContent = "Falhou";
        window.setTimeout(() => {
          button.textContent = "Copiar";
        }, 1500);
      }
    });
  });
}

function initCadastroForm() {
  const form = document.getElementById("cadastroForm");
  const feedback = document.getElementById("formFeedback");

  if (!form || !feedback) {
    return;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData();
    formData.append("nome", document.getElementById("nomeEstabelecimento").value);
    formData.append("tipo", document.getElementById("tipoEstabelecimento").value);
    formData.append("endereco", document.getElementById("endereco").value);
    formData.append("email", document.getElementById("email").value);
    formData.append("telefone", document.getElementById("telefone").value);
    formData.append("website", document.getElementById("website").value);
    formData.append("observacoes", document.getElementById("observacoes").value);
    formData.append("data_verificacao", document.getElementById("checkDate").value);
    formData.append("aceita_lightning", String(document.getElementById("acceptsLightning").checked));
    formData.append("aceita_onchain", String(document.getElementById("acceptsOnchain").checked));
    formData.append("aceita_contactless", String(document.getElementById("acceptsLightningContactless").checked));

    const logo = document.getElementById("logoEstabelecimento").files[0];
    if (logo) {
      formData.append("logo", logo);
    }

    try {
      const response = await fetch("/api/estabelecimentos", {
        method: "POST",
        body: formData,
      });

      const result = await response.json();
      if (!response.ok || !result.success) {
        throw new Error(result.message || "Não foi possível salvar o cadastro.");
      }

      showMessage(feedback, "success", result.message);
      form.reset();
      document.getElementById("checkDate").value = new Date().toISOString().split("T")[0];
      feedback.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      showMessage(feedback, "error", error.message || "Erro ao enviar o cadastro.");
      feedback.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

function initDirectory() {
  const root = document.getElementById("establishmentsDirectory");
  if (!root || !Array.isArray(window.establishmentsData)) {
    return;
  }

  const searchInput = document.getElementById("directorySearch");
  const typeInput = document.getElementById("directoryType");
  const paymentInput = document.getElementById("directoryPayment");
  const emptyState = document.getElementById("directoryEmptyState");

  function matchesFilters(item) {
    const search = (searchInput?.value || "").trim().toLowerCase();
    const selectedType = typeInput?.value || "";
    const selectedPayment = paymentInput?.value || "";

    const haystack = `${item.name} ${item.address} ${item.city_label}`.toLowerCase();
    const matchesSearch = !search || haystack.includes(search);
    const matchesType = !selectedType || item.type === selectedType;

    let matchesPayment = true;
    if (selectedPayment === "lightning") {
      matchesPayment = Boolean(item.accepts_lightning);
    } else if (selectedPayment === "onchain") {
      matchesPayment = Boolean(item.accepts_onchain);
    } else if (selectedPayment === "contactless") {
      matchesPayment = Boolean(item.accepts_contactless);
    }

    return matchesSearch && matchesType && matchesPayment;
  }

  function render() {
    const filtered = window.establishmentsData.filter(matchesFilters);
    root.innerHTML = filtered.map(establishmentCardTemplate).join("");

    if (emptyState) {
      emptyState.hidden = filtered.length > 0;
    }
  }

  [searchInput, typeInput, paymentInput].forEach((element) => {
    element?.addEventListener("input", render);
    element?.addEventListener("change", render);
  });

  render();
}

function initReveal() {
  const targets = document.querySelectorAll(
    ".hero-section, .page-panel, .hero-card, .metric-strip, .card-grid > *, .steps-grid > *, .team-grid > *"
  );

  if (!targets.length) {
    return;
  }

  if (!("IntersectionObserver" in window)) {
    targets.forEach((element) => element.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) {
          return;
        }

        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      });
    },
    { threshold: 0.12 }
  );

  targets.forEach((element) => {
    element.setAttribute("data-reveal", "");
    observer.observe(element);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initMobileMenu();
  initTabs();
  initCopyButtons();
  initCadastroForm();
  initDirectory();
  initReveal();
});
