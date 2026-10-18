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

function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function localDateISO() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
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
    badges.push('<span class="payment-badge is-contactless">Por aproximação</span>');
  }
  return badges.join("");
}

function establishmentCardTemplate(establishment) {
  const websiteUrl = establishment.website ? safeHttpUrl(establishment.website) : "";
  const website = websiteUrl
    ? `<a href="${escapeHtml(websiteUrl)}" target="_blank" rel="noopener nofollow ugc" class="inline-link">Abrir site</a>`
    : "";
  const logoUrl = safeHttpUrl(establishment.logo_url);
  const pilotPill = establishment.pilot ? '<span class="pill pill-brand">Caso piloto</span>' : "";

  return `
    <article class="establishment-card">
      <div class="establishment-logo-wrap">
        ${logoUrl ? `<img src="${escapeHtml(logoUrl)}" alt="${escapeHtml(establishment.name)}" class="establishment-logo" loading="lazy">` : ""}
      </div>
      <div class="establishment-body">
        <div class="badge-row">
          <span class="pill">${escapeHtml(establishment.type_label)}</span>
          ${pilotPill}
        </div>
        <h3>${escapeHtml(establishment.name)}</h3>
        <p>${escapeHtml(establishment.address)}</p>
        <div class="meta-row">
          <span><i class="fa-solid fa-location-dot" aria-hidden="true"></i> ${escapeHtml(establishment.city_label)}</span>
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

  function setOpen(open) {
    if (open) {
      menu.removeAttribute("hidden");
    } else {
      menu.setAttribute("hidden", "");
    }
    toggle.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", open ? "Fechar menu" : "Abrir menu");
  }

  toggle.addEventListener("click", () => setOpen(menu.hasAttribute("hidden")));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !menu.hasAttribute("hidden")) {
      setOpen(false);
      toggle.focus();
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

        triggers.forEach((item) => {
          item.classList.remove("is-active");
          item.setAttribute("aria-selected", "false");
        });
        panels.forEach((panel) => panel.classList.remove("is-active"));

        trigger.classList.add("is-active");
        trigger.setAttribute("aria-selected", "true");
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
      } catch (error) {
        button.textContent = "Falhou";
      }
      window.setTimeout(() => {
        button.textContent = "Copiar";
      }, 1500);
    });
  });
}

function initCadastroForm() {
  const form = document.getElementById("cadastroForm");
  const feedback = document.getElementById("formFeedback");
  if (!form || !feedback) {
    return;
  }

  const submitButton = form.querySelector('button[type="submit"]');
  const defaultButtonLabel = submitButton?.textContent ?? "";
  const websiteInput = document.getElementById("website");
  let isSubmitting = false;

  // Quem digita "www.loja.com" ganha o https:// na frente, em vez de um erro.
  websiteInput?.addEventListener("blur", () => {
    const value = websiteInput.value.trim();
    if (value && !/^[a-z][a-z0-9+.-]*:\/\//i.test(value)) {
      websiteInput.value = `https://${value}`;
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (isSubmitting) {
      return;
    }

    isSubmitting = true;
    feedback.hidden = true;
    feedback.textContent = "";
    form.setAttribute("aria-busy", "true");

    if (submitButton) {
      submitButton.disabled = true;
      submitButton.textContent = "Enviando...";
    }

    const formData = new FormData(form);
    formData.set("aceita_lightning", String(document.getElementById("acceptsLightning").checked));
    formData.set("aceita_onchain", String(document.getElementById("acceptsOnchain").checked));
    formData.set("aceita_contactless", String(document.getElementById("acceptsLightningContactless").checked));

    try {
      const response = await fetch("/api/estabelecimentos", {
        method: "POST",
        body: formData,
      });

      const responseText = await response.text();
      let result = {};

      if (responseText) {
        try {
          result = JSON.parse(responseText);
        } catch {
          result = {};
        }
      }

      if (!response.ok || !result.success) {
        const fallbackMessage =
          response.status === 413 ? "O arquivo passou do limite de 5 MB." : "Não foi possível salvar o cadastro.";
        throw new Error(result.message || fallbackMessage);
      }

      showMessage(feedback, "success", result.message);
      form.reset();
      document.getElementById("checkDate").value = localDateISO();
    } catch (error) {
      showMessage(feedback, "error", error.message || "Erro ao enviar o cadastro.");
    } finally {
      feedback.scrollIntoView({ behavior: "smooth", block: "start" });
      isSubmitting = false;
      form.removeAttribute("aria-busy");

      if (submitButton) {
        submitButton.disabled = false;
        submitButton.textContent = defaultButtonLabel;
      }
    }
  });
}

function readJsonData(elementId) {
  const element = document.getElementById(elementId);
  if (!element) {
    return null;
  }
  try {
    return JSON.parse(element.textContent || "null");
  } catch {
    return null;
  }
}

function initDirectory() {
  const root = document.getElementById("establishmentsDirectory");
  const establishments = readJsonData("establishments-data");
  if (!root || !Array.isArray(establishments)) {
    return;
  }

  const searchInput = document.getElementById("directorySearch");
  const typeInput = document.getElementById("directoryType");
  const paymentInput = document.getElementById("directoryPayment");
  const emptyState = document.getElementById("directoryEmptyState");

  function normalize(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase();
  }

  function matchesFilters(item) {
    const search = normalize(searchInput?.value.trim());
    const selectedType = typeInput?.value || "";
    const selectedPayment = paymentInput?.value || "";

    const haystack = normalize(`${item.name} ${item.address} ${item.city_label}`);
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
    const filtered = establishments.filter(matchesFilters);
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
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || !("IntersectionObserver" in window)) {
    return;
  }

  // So os blocos curtos animam: texto longo (trilha Aprenda) aparece direto.
  const targets = document.querySelectorAll(
    ".card-grid > *, .steps-grid > *, .trail-grid > *, .team-grid > *, .metric-strip > *"
  );

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
    { threshold: 0, rootMargin: "0px 0px -40px 0px" }
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
