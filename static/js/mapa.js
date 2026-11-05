/* O mapa dos lugares que aceitam bitcoin (04/11/2026): pontos do BTC Map e os cadastros do projeto,
   agrupados por proximidade, com filtro pela legenda, busca por cidade ou lugar, a lista do que está à vista
   e a janela com os detalhes de cada lugar. Tudo do próprio site: a CSP só aceita script daqui. */
(function () {
  "use strict";

  var canvas = document.getElementById("map-canvas");
  if (!canvas || !window.L || !L.markerClusterGroup) return;

  var OURS = 0, RECENT = 1, STALE = 2;
  var STATUS_TEXT = ["Conferido pelo projeto", "Confirmado no último ano", "Sem confirmação recente"];
  var LIST_LIMIT = 80;          // lugares na lista ao lado; acima disso, de longe, a lista mostra cidades
  var LIST_ZOOM = 12;           // de perto (uma cidade), a lista mostra os lugares mesmo sendo muitos
  var LIST_MAX = 300;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var listTitle = document.getElementById("map-list-title");
  var listNote = document.getElementById("map-list-note");
  var listItems = document.getElementById("map-list-items");
  var search = document.getElementById("map-search");
  var suggestions = document.getElementById("map-suggestions");
  var locateBtn = document.getElementById("map-locate");
  var dialog = document.getElementById("place-dialog");

  var points = [];              // [ref, lat, lon, status, nome, tipo, municipio, uf]
  var byRef = {};
  var markers = [[], [], []];
  var visible = [true, true, true];
  var cities = null;            // [nome, uf, lat, lon, n], carregadas na primeira busca
  var youMarker = null;

  // ---------- utilidades ----------
  function norm(text) {
    return (text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[-']/g, " ").replace(/\s+/g, " ").trim();
  }
  function milhar(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, "."); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function move(target, zoom) {
    if (Array.isArray(target[0])) {
      map.fitBounds(target, { animate: !reduceMotion, maxZoom: zoom || 16, padding: [24, 24] });
    } else {
      map.setView(target, zoom, { animate: !reduceMotion });
    }
  }

  // ---------- o mapa ----------
  var focus = JSON.parse(canvas.getAttribute("data-focus"));
  canvas.innerHTML = "";
  var map = L.map(canvas, { zoomControl: true, scrollWheelZoom: false, worldCopyJump: false, minZoom: 3 });
  map.fitBounds(focus, { animate: false });
  // Quadros do OpenStreetMap, escurecidos no CSS (.map-canvas .leaflet-tile-pane) para o tema do site.
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">colaboradores do OpenStreetMap</a>' +
      ' · lugares: <a href="https://btcmap.org/">BTC Map</a>'
  }).addTo(map);
  map.attributionControl.setPrefix(false);

  // A roda do mouse só dá zoom depois de clicar no mapa: assim a página rola normalmente por cima dele.
  map.on("click focus", function () { map.scrollWheelZoom.enable(); });
  canvas.addEventListener("mouseleave", function () { map.scrollWheelZoom.disable(); });

  var BOLT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13.5 2 5 13.2h5.6L9.8 22 19 10.4h-5.7z"/></svg>';
  var icons = [
    L.divIcon({ className: "pin pin-ours", html: '<span class="pin-ours-body">' + BOLT + "</span>", iconSize: [30, 36], iconAnchor: [15, 34] }),
    L.divIcon({ className: "pin pin-dot is-recent", html: "<span></span>", iconSize: [16, 16], iconAnchor: [8, 8] }),
    L.divIcon({ className: "pin pin-dot is-stale", html: "<span></span>", iconSize: [12, 12], iconAnchor: [6, 6] })
  ];

  // O grupo vira uma rosca: verde é a parte confirmada no último ano; o raio aparece se tem cadastro nosso.
  var cluster = L.markerClusterGroup({
    showCoverageOnHover: false, maxClusterRadius: 48, spiderfyOnMaxZoom: true, chunkedLoading: true,
    disableClusteringAtZoom: 17,
    iconCreateFunction: function (group) {
      var children = group.getAllChildMarkers();
      var count = [0, 0, 0];
      children.forEach(function (m) { count[m.options.status] += 1; });
      var total = children.length;
      // em passos de 10%, como classe: a CSP do site não deixa usar style="" no HTML
      var green = Math.round(((count[OURS] + count[RECENT]) / total) * 10);
      var size = total < 10 ? 36 : total < 100 ? 44 : total < 500 ? 54 : 62;
      var html = '<span class="cluster-ring ok-' + green + '"><span class="cluster-core">' +
        (total >= 1000 ? Math.round(total / 100) / 10 + "k" : total) + "</span></span>" +
        (count[OURS] ? '<span class="cluster-ours">' + BOLT + "</span>" : "");
      return L.divIcon({ className: "pin-cluster", html: html, iconSize: [size, size] });
    }
  });
  map.addLayer(cluster);

  function addPoints(data) {
    points = data;
    points.forEach(function (p) {
      byRef[String(p[0])] = p;
      var marker = L.marker([p[1], p[2]], {
        icon: icons[p[3]], status: p[3], ref: String(p[0]), title: p[4], keyboard: false,
        zIndexOffset: p[3] === OURS ? 1000 : p[3] === RECENT ? 100 : 0
      });
      marker.on("click", function () { openPlace(String(p[0])); });
      markers[p[3]].push(marker);
    });
    [OURS, RECENT, STALE].forEach(function (s) { if (visible[s]) cluster.addLayers(markers[s]); });
    refreshList();
    openFromHash();
  }

  // ---------- a legenda filtra ----------
  Array.prototype.forEach.call(document.querySelectorAll(".legend-chip"), function (chip) {
    chip.addEventListener("click", function () {
      var s = Number(chip.getAttribute("data-status"));
      visible[s] = !visible[s];
      chip.setAttribute("aria-pressed", String(visible[s]));
      if (visible[s]) cluster.addLayers(markers[s]); else cluster.removeLayers(markers[s]);
      refreshList();
    });
  });

  // ---------- a lista do que está à vista ----------
  function inView() {
    var bounds = map.getBounds();
    return points.filter(function (p) { return visible[p[3]] && bounds.contains([p[1], p[2]]); });
  }

  function refreshList() {
    if (!points.length) return;
    var shown = inView();
    var counts = [0, 0, 0];
    points.forEach(function (p) {
      if (map.getBounds().contains([p[1], p[2]])) counts[p[3]] += 1;
    });
    Array.prototype.forEach.call(document.querySelectorAll(".legend-count"), function (b) {
      b.textContent = milhar(counts[Number(b.getAttribute("data-count"))]);
    });
    listItems.innerHTML = "";
    if (!shown.length) {
      listTitle.textContent = "Nenhum lugar nesta área";
      listNote.textContent = "";
      var li = el("li", "map-empty");
      li.appendChild(el("p", null, "Conhece um comércio daqui que aceita bitcoin? Coloque no mapa."));
      var a = el("a", "btn btn-secondary", "Cadastrar um lugar");
      a.href = canvas.getAttribute("data-cadastro");
      li.appendChild(a);
      listItems.appendChild(li);
      return;
    }
    if (shown.length > LIST_LIMIT && map.getZoom() < LIST_ZOOM) {
      var grouped = {};
      shown.forEach(function (p) {
        var key = p[6] + "|" + p[7];
        var g = grouped[key] || (grouped[key] = { name: p[6], uf: p[7], n: 0, lat: 0, lon: 0 });
        g.n += 1; g.lat += p[1]; g.lon += p[2];
      });
      var list = Object.keys(grouped).map(function (k) { return grouped[k]; })
        .sort(function (a, b) { return b.n - a.n || a.name.localeCompare(b.name); }).slice(0, 40);
      listTitle.textContent = "Cidades nesta área";
      listNote.textContent = milhar(shown.length) + " lugares";
      list.forEach(function (g) {
        var item = el("li");
        var button = el("button", "map-city");
        button.type = "button";
        var label = el("span", null, g.name + " ");
        label.appendChild(el("small", null, g.uf));
        button.appendChild(label);
        button.appendChild(el("span", "city-count", milhar(g.n)));
        button.addEventListener("click", function () { goToCity(g.name, g.uf, g.lat / g.n, g.lon / g.n); });
        item.appendChild(button);
        listItems.appendChild(item);
      });
      return;
    }
    shown.sort(function (a, b) { return a[3] - b[3] || a[4].localeCompare(b[4]); });
    listTitle.textContent = "Lugares nesta área";
    listNote.textContent = shown.length > LIST_MAX ? LIST_MAX + " de " + milhar(shown.length) : milhar(shown.length);
    shown.slice(0, LIST_MAX).forEach(function (p) {
      var item = el("li");
      var link = el("a", "map-item");
      link.href = "#lugar-" + p[0];
      link.setAttribute("data-ref", String(p[0]));
      link.appendChild(el("span", "list-dot s" + p[3]));
      var text = el("span", "map-item-text");
      text.appendChild(el("strong", null, p[4]));
      text.appendChild(el("small", null, p[5] + " · " + p[6]));
      link.appendChild(text);
      item.appendChild(link);
      listItems.appendChild(item);
    });
  }
  var listTimer = null;
  map.on("moveend", function () { clearTimeout(listTimer); listTimer = setTimeout(refreshList, 120); });

  listItems.addEventListener("click", function (event) {
    var item = event.target.closest(".map-item");
    if (item) {
      event.preventDefault();
      openPlace(item.getAttribute("data-ref"), true);
      return;
    }
    var city = event.target.closest("a.map-city");
    if (city) {
      event.preventDefault();
      goToCity(city.getAttribute("data-name"), city.getAttribute("data-uf"),
        Number(city.getAttribute("data-lat")), Number(city.getAttribute("data-lon")));
    }
  });

  function goToCity(name, uf, lat, lon) {
    var here = points.filter(function (p) { return p[6] === name && p[7] === uf; });
    if (here.length > 1) {
      var lats = here.map(function (p) { return p[1]; }), lons = here.map(function (p) { return p[2]; });
      move([[Math.min.apply(null, lats), Math.min.apply(null, lons)], [Math.max.apply(null, lats), Math.max.apply(null, lons)]], 15);
    } else if (here.length === 1) {
      move([here[0][1], here[0][2]], 15);
    } else {
      move([lat, lon], 13);
      setTimeout(function () {
        listTitle.textContent = "Ainda não há lugares em " + name;
      }, reduceMotion ? 50 : 900);
    }
    canvas.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "nearest" });
  }

  // ---------- busca por cidade ou lugar ----------
  var options = [], active = -1;

  function loadCities() {
    if (cities) return Promise.resolve(cities);
    return fetch(canvas.getAttribute("data-cities")).then(function (r) { return r.json(); })
      .then(function (rows) {
        cities = rows.map(function (row) { return { name: row[0], uf: row[1], lat: row[2], lon: row[3], n: row[4], key: norm(row[0]) }; });
        return cities;
      });
  }

  function closeSuggestions() {
    suggestions.hidden = true;
    search.setAttribute("aria-expanded", "false");
    active = -1;
  }

  function renderSuggestions() {
    var q = norm(search.value);
    suggestions.innerHTML = "";
    options = [];
    if (q.length < 2) { closeSuggestions(); return; }
    (cities || []).filter(function (c) { return c.key.indexOf(q) === 0; })
      .sort(function (a, b) { return b.n - a.n || a.name.localeCompare(b.name); }).slice(0, 6)
      .forEach(function (c) { options.push({ kind: "city", city: c }); });
    var placesFound = 0;
    for (var i = 0; i < points.length && placesFound < 5; i += 1) {
      if (norm(points[i][4]).indexOf(q) !== -1) { options.push({ kind: "place", point: points[i] }); placesFound += 1; }
    }
    if (!options.length) {
      var none = el("li", "suggestion-empty", "Nada encontrado. Tente o nome da cidade sem abreviar.");
      suggestions.appendChild(none);
    }
    options.forEach(function (opt, index) {
      var li = el("li", "suggestion");
      li.id = "map-suggestion-" + index;
      li.setAttribute("role", "option");
      if (opt.kind === "city") {
        li.appendChild(el("i", "fa-solid fa-city"));
        li.appendChild(el("span", "suggestion-main", opt.city.name + " · " + opt.city.uf));
        li.appendChild(el("span", "suggestion-side", opt.city.n ? milhar(opt.city.n) + (opt.city.n === 1 ? " lugar" : " lugares") : "nenhum ainda"));
      } else {
        li.appendChild(el("span", "list-dot s" + opt.point[3]));
        li.appendChild(el("span", "suggestion-main", opt.point[4]));
        li.appendChild(el("span", "suggestion-side", opt.point[6] + " · " + opt.point[7]));
      }
      li.addEventListener("mousedown", function (event) { event.preventDefault(); choose(index); });
      suggestions.appendChild(li);
    });
    suggestions.hidden = false;
    search.setAttribute("aria-expanded", "true");
  }

  function highlight(index) {
    active = index;
    Array.prototype.forEach.call(suggestions.querySelectorAll(".suggestion"), function (li, i) {
      li.classList.toggle("is-active", i === index);
      li.setAttribute("aria-selected", String(i === index));
    });
    if (index >= 0) search.setAttribute("aria-activedescendant", "map-suggestion-" + index);
    else search.removeAttribute("aria-activedescendant");
  }

  function choose(index) {
    var opt = options[index];
    if (!opt) return;
    closeSuggestions();
    if (opt.kind === "city") {
      search.value = opt.city.name + " · " + opt.city.uf;
      goToCity(opt.city.name, opt.city.uf, opt.city.lat, opt.city.lon);
    } else {
      search.value = opt.point[4];
      openPlace(String(opt.point[0]), true);
    }
  }

  search.addEventListener("focus", function () { loadCities().then(renderSuggestions).catch(function () {}); });
  search.addEventListener("input", function () { loadCities().then(renderSuggestions).catch(renderSuggestions); });
  search.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown") { event.preventDefault(); highlight(Math.min(active + 1, options.length - 1)); }
    else if (event.key === "ArrowUp") { event.preventDefault(); highlight(Math.max(active - 1, 0)); }
    else if (event.key === "Enter") { event.preventDefault(); choose(active >= 0 ? active : 0); }
    else if (event.key === "Escape") { closeSuggestions(); }
  });
  search.addEventListener("blur", function () { setTimeout(closeSuggestions, 120); });

  // ---------- perto de mim ----------
  if (navigator.geolocation && window.isSecureContext !== false) {
    locateBtn.hidden = false;
    locateBtn.addEventListener("click", function () {
      locateBtn.disabled = true;
      navigator.geolocation.getCurrentPosition(function (pos) {
        locateBtn.disabled = false;
        var here = [pos.coords.latitude, pos.coords.longitude];
        if (youMarker) youMarker.remove();
        youMarker = L.circleMarker(here, { radius: 8, color: "#ffffff", weight: 2, fillColor: "#4ea3ff", fillOpacity: 1 })
          .addTo(map).bindTooltip("Você está aqui");
        move(here, 14);
      }, function () {
        locateBtn.disabled = false;
        listTitle.textContent = "Não deu para saber onde você está";
        listNote.textContent = "busque a cidade";
      }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
    });
  }

  // ---------- a janela do lugar ----------
  var DAYS = { Mo: "seg", Tu: "ter", We: "qua", Th: "qui", Fr: "sex", Sa: "sáb", Su: "dom", PH: "feriados" };
  function hours(text) {
    return text.replace(/\b(Mo|Tu|We|Th|Fr|Sa|Su|PH)\b/g, function (d) { return DAYS[d]; })
      .replace(/\boff\b/g, "fechado").replace(/24\/7/, "todos os dias, 24 horas").replace(/;\s*/g, " · ");
  }
  function fact(list, label, value, href) {
    if (!value) return;
    list.appendChild(el("dt", null, label));
    var dd = el("dd");
    if (href) {
      var a = el("a", "inline-link", value);
      a.href = href;
      if (/^https?:/.test(href)) { a.target = "_blank"; a.rel = "noopener nofollow ugc"; }
      dd.appendChild(a);
    } else {
      dd.textContent = value;
    }
    list.appendChild(dd);
  }
  function action(container, label, href, cls, icon) {
    var a = el("a", cls);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    if (icon) a.appendChild(el("i", "fa-solid " + icon));
    a.appendChild(document.createTextNode(" " + label));
    container.appendChild(a);
  }

  var lastFocus = null;
  function openPlace(ref, fly) {
    var p = byRef[ref];
    if (!p) return;
    if (fly) move([p[1], p[2]], Math.max(map.getZoom(), 17));
    lastFocus = document.activeElement;
    document.getElementById("place-type").textContent = p[5];
    var flag = document.getElementById("place-status");
    flag.textContent = STATUS_TEXT[p[3]];
    flag.className = "place-flag s" + p[3];
    document.getElementById("place-name").textContent = p[4];
    document.getElementById("place-address").textContent = p[6] + " · " + p[7];
    ["place-payments", "place-facts", "place-actions"].forEach(function (id) { document.getElementById(id).innerHTML = ""; });
    document.getElementById("place-logo").hidden = true;
    document.getElementById("place-note").hidden = true;
    if (typeof dialog.showModal === "function") { if (!dialog.open) dialog.showModal(); } else { dialog.setAttribute("open", ""); }
    history.replaceState(null, "", "#lugar-" + ref);

    fetch(canvas.getAttribute("data-place").replace("REF", encodeURIComponent(ref)))
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { if (byRef[ref] === p && dialog.open) fillPlace(ref, d); })
      .catch(function () {
        var note = document.getElementById("place-note");
        note.textContent = "Não deu para carregar os detalhes agora. Tente de novo em instantes.";
        note.hidden = false;
      });
  }

  function fillPlace(ref, d) {
    var flag = document.getElementById("place-status");
    flag.textContent = d.status;
    document.getElementById("place-type").textContent = d.tipo;
    document.getElementById("place-name").textContent = d.nome;
    var city = [d.municipio, d.uf].filter(Boolean).join(" - ");
    document.getElementById("place-address").textContent = d.endereco ? d.endereco + (d.endereco.indexOf(d.municipio) === -1 ? " · " + city : "") : city;
    if (d.logo) {
      var logo = document.getElementById("place-logo");
      logo.onerror = function () { logo.hidden = true; };
      logo.src = d.logo;
      logo.alt = d.nome;
      logo.hidden = false;
    }
    var pay = document.getElementById("place-payments");
    if (d.lightning) pay.appendChild(el("span", "payment-badge is-lightning", "Lightning"));
    if (d.onchain) pay.appendChild(el("span", "payment-badge is-onchain", "On-chain"));
    if (d.aproximacao) pay.appendChild(el("span", "payment-badge is-contactless", "Por aproximação"));
    var facts = document.getElementById("place-facts");
    fact(facts, "Horário", d.horario ? hours(d.horario) : "");
    var phone = (d.telefone || "").split(";")[0].trim();
    fact(facts, "Telefone", phone, phone ? "tel:" + phone.replace(/[^\d+]/g, "") : null);
    fact(facts, "Site", d.site ? d.site.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "") : "", d.site || null);
    if (d.aproximado) {
      var note = document.getElementById("place-note");
      note.textContent = "Posição aproximada: o ponto está no centro de " + d.municipio + ". O endereço acima é o certo.";
      note.hidden = false;
    }
    var actions = document.getElementById("place-actions");
    var dest = d.lat + "," + d.lon;
    action(actions, "Como chegar", "https://www.google.com/maps/dir/?api=1&destination=" + dest, "btn btn-cta", "fa-route");
    if (d.btcmap) action(actions, "Ver no BTC Map", d.btcmap, "btn btn-secondary", "fa-map-location-dot");
    if (d.confirmar) action(actions, "Passou lá? Confirme", d.confirmar, "inline-link", "fa-circle-check");
    var share = el("button", "inline-link place-share");
    share.type = "button";
    share.appendChild(el("i", "fa-solid fa-share-nodes"));
    share.appendChild(document.createTextNode(" Compartilhar"));
    share.addEventListener("click", function () {
      var url = location.origin + location.pathname + "#lugar-" + ref;
      if (navigator.share) {
        navigator.share({ title: d.nome + " aceita bitcoin", url: url }).catch(function () {});
      } else if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function () { share.lastChild.textContent = " Link copiado"; });
      }
    });
    actions.appendChild(share);
  }

  function closePlace() {
    if (dialog.open) { if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open"); }
  }
  dialog.addEventListener("close", function () {
    if (/^#lugar-/.test(location.hash)) history.replaceState(null, "", location.pathname + location.search + "#mapa");
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  });
  document.getElementById("place-close").addEventListener("click", closePlace);
  dialog.addEventListener("click", function (event) { if (event.target === dialog) closePlace(); });

  function openFromHash() {
    var match = /^#lugar-(a?\d+)$/.exec(location.hash);
    if (match && byRef[match[1]]) openPlace(match[1], true);
  }
  window.addEventListener("hashchange", openFromHash);

  // ---------- os pontos ----------
  fetch(canvas.getAttribute("data-points"))
    .then(function (r) { return r.json(); })
    .then(function (data) { addPoints(data.pontos || []); })
    .catch(function () {
      listTitle.textContent = "Não deu para carregar o mapa agora";
      listNote.textContent = "tente de novo em instantes";
    });
})();
