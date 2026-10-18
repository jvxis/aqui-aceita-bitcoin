(function () {
  'use strict';

  // Aviso de cookies e Google Analytics 4. O GA só carrega depois do "Aceitar"; a escolha fica no
  // navegador por 180 dias e pode ser mudada em "Preferências de cookies", no rodapé.
  var script = document.currentScript;
  var ANALYTICS_ID = script && script.getAttribute('data-analytics-id');
  var KEY = 'aceita_consent';
  var VERSION = 'v1';
  var MAX_AGE_MS = 180 * 24 * 60 * 60 * 1000;

  function read() {
    try {
      var value = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (value && value.v === VERSION && Date.now() - value.at < MAX_AGE_MS) return value;
    } catch (e) {}
    return null;
  }

  function save(analytics) {
    try {
      localStorage.setItem(KEY, JSON.stringify({v: VERSION, analytics: analytics, at: Date.now()}));
    } catch (e) {}
  }

  function loadAnalytics() {
    if (!ANALYTICS_ID || document.getElementById('aceita-ga')) return;
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { window.dataLayer.push(arguments); };
    window.gtag('js', new Date());
    window.gtag('config', ANALYTICS_ID, {page_location: location.origin + location.pathname + location.search});
    var tag = document.createElement('script');
    tag.id = 'aceita-ga';
    tag.async = true;
    tag.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(ANALYTICS_ID);
    document.head.appendChild(tag);
  }

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    var banner = document.getElementById('consent-banner');
    var consent = read();
    if (consent && consent.analytics) loadAnalytics();
    if (!banner) return;
    banner.hidden = Boolean(consent);

    function choose(analytics) {
      save(analytics);
      banner.hidden = true;
      if (analytics) loadAnalytics();
      // Quem recusa depois de ter aceito: o GA já carregado só some ao recarregar a página.
      else if (document.getElementById('aceita-ga')) location.reload();
    }

    document.getElementById('consent-accept').addEventListener('click', function () { choose(true); });
    document.getElementById('consent-reject').addEventListener('click', function () { choose(false); });
    var open = document.getElementById('consent-open');
    if (open) open.addEventListener('click', function () { banner.hidden = false; });
  });
})();
