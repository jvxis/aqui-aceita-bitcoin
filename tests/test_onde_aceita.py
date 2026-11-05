"""Páginas por estado e cidade com os lugares do BTC Map (04/11/2026)."""
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

import btcmap_sync

# Um quadrado grosseiro em volta do Brasil basta para o teste do contorno.
BRASIL = {"type": "Polygon", "coordinates": [[[-74, -34], [-34, -34], [-34, 5], [-74, 5], [-74, -34]]]}
MUNICIPIOS = [
    {"codigo": 3550308, "nome": "São Paulo", "uf": "SP", "lat": -23.5329, "lon": -46.6395},
    {"codigo": 3552809, "nome": "Taboão da Serra", "uf": "SP", "lat": -23.6019, "lon": -46.7526},
    {"codigo": 3549300, "nome": "Santo Antônio do Pinhal", "uf": "SP", "lat": -22.827, "lon": -45.663},
    {"codigo": 4314902, "nome": "Porto Alegre", "uf": "RS", "lat": -30.0318, "lon": -51.2065},
]
for _m in MUNICIPIOS:
    _m["chave"] = btcmap_sync.normalize_name(_m["nome"])


def place(id_, lat, lon, name="Café", city=None, verified=None, updated="2026-11-01T00:00:00Z", **extra):
    data = {"id": id_, "lat": lat, "lon": lon, "name": name, "icon": "local_cafe", "updated_at": updated,
            "verified_at": verified, "osm:addr:city": city, "osm:payment:lightning": "yes"}
    data.update(extra)
    return data


class FakeApi:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if "/v3/areas/" in url:
            return {"id": 173, "tags": {"geo_json": BRASIL}}
        return self.batches.pop(0)


def test_sync_full_then_incremental(tmp_path):
    conn = btcmap_sync.connect(tmp_path / "btcmap.db")
    api = FakeApi(
        [place(1, -23.60, -46.75, city="São Paulo"),          # perto de Taboão, mas a etiqueta diz São Paulo
         place(2, -23.60, -46.75),                             # sem etiqueta: a sede mais próxima
         place(3, 40.4, -3.7, name="Madri"),                   # fora do Brasil
         place(4, -30.03, -51.2, city="Porto Alegre", updated="2026-11-02T00:00:00Z")],
        [place(4, -30.03, -51.2, deleted_at="2026-11-03T00:00:00Z", updated="2026-11-03T00:00:00Z"),
         place(5, -22.83, -45.66, city="Santo Antonio do pinhal", updated="2026-11-03T01:00:00Z")],
    )
    first = btcmap_sync.sync(conn, api, MUNICIPIOS, now=1_000_000)
    assert first["gravados"] == 3 and first["total"] == 3
    rows = {r["id"]: (r["municipio"], r["uf"]) for r in conn.execute("SELECT * FROM lugares")}
    assert rows == {1: ("São Paulo", "SP"), 2: ("Taboão da Serra", "SP"), 4: ("Porto Alegre", "RS")}
    assert "include_deleted" not in api.urls[1]
    second = btcmap_sync.sync(conn, api, MUNICIPIOS, now=1_000_100)
    # a segunda vez pede só o que mudou desde o último updated_at, com os removidos
    assert "updated_since=2026-11-02T00%3A00%3A00Z" in api.urls[-1] and "include_deleted=true" in api.urls[-1]
    assert second["removidos"] == 1
    rows = {r["id"]: r["municipio"] for r in conn.execute("SELECT * FROM lugares")}
    assert rows == {1: "São Paulo", 2: "Taboão da Serra", 5: "Santo Antônio do Pinhal"}
    # o contorno fica guardado: não é pedido de novo na mesma semana
    assert sum("/v3/areas/" in url for url in api.urls) == 1


def test_city_tag_far_away_is_ignored():
    locator = btcmap_sync.Locator(MUNICIPIOS)
    # etiqueta "Porto Alegre" num ponto em São Paulo: vale a sede mais próxima
    assert locator.locate(-23.53, -46.64, "Porto Alegre")["nome"] == "São Paulo"


def seed(path, places_by_city):
    conn = btcmap_sync.connect(path)
    next_id = 1
    for (municipio, uf), items in places_by_city.items():
        for verified in items:
            conn.execute(
                "INSERT INTO lugares (id, nome, icone, lat, lon, endereco, verificado_em, lightning, municipio, uf) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (next_id, f"Lugar {next_id}", "restaurant", -23.5, -46.6, f"Rua {next_id}", verified, 1, municipio, uf),
            )
            next_id += 1
    btcmap_sync.set_meta(conn, "sincronizado_em", "2026-11-04T08:00:00+00:00")
    conn.commit()
    conn.close()


@pytest.fixture
def onde(app_module, tmp_path):
    recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=900)).strftime("%Y-%m-%d")
    seed(tmp_path / "btcmap.db", {
        ("São Paulo", "SP"): [recent] * 3 + [old] * 8,
        ("Ubatuba", "SP"): [recent, None],
        ("Porto Alegre", "RS"): [old] * 2,
        ("Belo Horizonte", "MG"): [recent] * 12,
    })
    app_module.app.config["BTCMAP_DB_PATH"] = str(tmp_path / "btcmap.db")
    app_module.app.config["ONDE_ACEITA_ESTADOS"] = ["RS", "SP"]
    app_module.app.config["ONDE_ACEITA_INDEXAR"] = False
    yield app_module
    app_module.app.config["ONDE_ACEITA_INDEXAR"] = False


def test_state_page(onde, client):
    html = client.get("/onde-aceita/sp").get_data(as_text=True)
    assert "Lugares que aceitam bitcoin no estado de São Paulo" in html
    assert "13 lugares em 2 cidades" in html and "4 foram confirmados" in html
    # o mapa, já aproximado no estado, com a lista dos lugares pronta no HTML
    assert 'id="map-canvas"' in html and "data-focus=" in html and "js/mapa.js" in html
    assert html.count('class="map-item"') == 13 and 'href="#lugar-1"' in html
    assert "colaboradores do OpenStreetMap" in html and "Atualizado em 04/11/2026" in html
    assert 'content="noindex,follow"' in html
    assert "faucet/?origem=aceita" in html


def test_city_page_and_redirects(onde, client):
    page = client.get("/onde-aceita/sp/sao-paulo")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Lugares que aceitam bitcoin em São Paulo" in html
    assert html.count('class="map-item"') == 11
    # os confirmados há pouco vêm antes dos velhos
    assert html.index("list-dot s1") < html.index("list-dot s2")
    small = client.get("/onde-aceita/sp/ubatuba")
    assert small.status_code == 302 and small.headers["Location"].endswith("/onde-aceita/sp?cidade=ubatuba")
    assert client.get("/onde-aceita/sp?cidade=ubatuba").status_code == 200
    assert client.get("/onde-aceita/SP").status_code == 301
    assert client.get("/onde-aceita/sp/nao-existe").status_code == 404
    # MG tem lugares, mas não está na lista dos publicados
    assert client.get("/onde-aceita/mg").status_code == 404
    assert client.get("/onde-aceita/xx").status_code == 404


def test_country_and_directory_became_the_home_map(onde, client):
    for path in ("/onde-aceita", "/estabelecimentos", "/estabelecimentos.html"):
        assert client.get(path).status_code == 301
    assert client.get("/onde-aceita").headers["Location"].endswith("/#mapa")
    html = client.get("/").get_data(as_text=True)
    assert 'id="mapa"' in html and 'id="place-dialog"' in html
    # números do Brasil inteiro no hero, e as cidades com mais lugares na lista ao lado do mapa
    assert "27</dd>" in html and "3 estados" in html
    assert html.index("São Paulo <small>SP") < html.index("Porto Alegre <small>RS")
    assert 'href="/onde-aceita/sp/sao-paulo"' in html and 'href="/onde-aceita/sp?cidade=ubatuba"' in html
    # Belo Horizonte tem lugares, mas MG não está no ar: fica fora da lista
    assert "Belo Horizonte" not in html
    # o menu "Onde aceita" leva ao mapa
    assert 'href="/#mapa"' in html


def test_map_json_points_and_cities(onde, client):
    data = client.get("/onde-aceita/mapa.json").get_json()
    assert len(data["pontos"]) == 27 and data["atualizado"] == "04/11/2026"
    assert {point[3] for point in data["pontos"]} == {1, 2}
    cities = client.get("/onde-aceita/cidades.json").get_json()
    assert len(cities) > 5000 and cities[0][:2] == ["Belo Horizonte", "MG"] and cities[0][4] == 12
    assert client.get("/onde-aceita/cidades.json").headers["Cache-Control"] == "public, max-age=3600"


def test_place_details(onde, client):
    place = client.get("/onde-aceita/lugar/1.json").get_json()
    assert place["origem"] == "btcmap" and place["nome"] == "Lugar 1" and place["lightning"] is True
    assert place["confirmar"].endswith("id=1") and place["status"].startswith("Confirmado em")
    assert client.get("/onde-aceita/lugar/999.json").status_code == 404
    assert client.get("/onde-aceita/lugar/abc.json").status_code == 404


def test_our_establishments_on_the_map(onde, client):
    with sqlite3.connect(onde.app.config["DB_FILE_PATH"]) as conn:
        conn.execute("INSERT INTO estabelecimentos (nome, tipo, endereco, aceita_lightning, status) VALUES "
                     "('Clube', 'other', 'Av. Paulista, 100 - Bela Vista, São Paulo - SP, 01310-000', 1, 'aprovado'),"
                     "('Pendente', 'other', 'Rua A, 1 - Centro, São Paulo - SP, 01000-000', 1, 'pendente'),"
                     "('Gaúcho', 'cafe', 'Rua B, 2 - Centro, Porto Alegre - RS, 90000-000', 1, 'aprovado'),"
                     "('Sem cidade', 'cafe', 'Rua Presidente Antônio Cândido', 1, 'aprovado')")
    points = client.get("/onde-aceita/mapa.json").get_json()["pontos"]
    ours = [point for point in points if point[3] == 0]
    assert sorted(point[4] for point in ours) == ["Clube", "Gaúcho"]
    clube = next(point for point in ours if point[4] == "Clube")
    detail = client.get(f"/onde-aceita/lugar/{clube[0]}.json").get_json()
    # sem posição achada ainda, o ponto fica no centro da cidade e a janela avisa
    assert detail["origem"] == "projeto" and detail["aproximado"] is True and detail["municipio"] == "São Paulo"
    sp = client.get("/onde-aceita/sp/sao-paulo").get_data(as_text=True)
    assert "Clube" in sp and "Pendente" not in sp and "Gaúcho" not in sp
    assert "Gaúcho" in client.get("/onde-aceita/rs").get_data(as_text=True)


def test_our_place_and_its_btcmap_twin_are_one_point(onde, client, tmp_path):
    conn = btcmap_sync.connect(tmp_path / "btcmap.db")
    conn.execute("UPDATE lugares SET lat=-23.57047, lon=-46.64494, horario='Mo-Fr 09:00-18:00', onchain=1 WHERE id=1")
    conn.commit()
    conn.close()
    with sqlite3.connect(onde.app.config["DB_FILE_PATH"]) as conn:
        conn.execute("INSERT INTO estabelecimentos (nome, tipo, endereco, aceita_lightning, status, lat, lon) VALUES "
                     "('Clube', 'other', 'Av. Paulista, 100, São Paulo - SP', 1, 'aprovado', -23.570469, -46.6449397)")
    refs = [point[0] for point in client.get("/onde-aceita/mapa.json").get_json()["pontos"]]
    assert 1 not in refs and len(refs) == 27
    ours = next(ref for ref in refs if str(ref).startswith("a"))
    detail = client.get(f"/onde-aceita/lugar/{ours}.json").get_json()
    assert detail["btcmap"].endswith("/merchant/1") and detail["onchain"] is True
    assert detail["horario"] == "Mo-Fr 09:00-18:00" and detail["aproximado"] is False
    assert 'href="#lugar-1"' not in client.get("/onde-aceita/sp/sao-paulo").get_data(as_text=True)


def test_sitemap_only_when_indexing(onde, client):
    assert "onde-aceita" not in client.get("/sitemap.xml").get_data(as_text=True)
    onde.app.config["ONDE_ACEITA_INDEXAR"] = True
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/onde-aceita/sp</loc>" in sitemap and "/onde-aceita/sp/sao-paulo</loc>" in sitemap
    assert "ubatuba" not in sitemap and "/onde-aceita/mg" not in sitemap
    # o que virou redirecionamento sai do sitemap
    assert "/onde-aceita</loc>" not in sitemap and "/estabelecimentos" not in sitemap
    assert 'content="index,follow"' in client.get("/onde-aceita/rs").get_data(as_text=True)


def test_without_btcmap_database(onde, client, tmp_path):
    onde.app.config["BTCMAP_DB_PATH"] = str(tmp_path / "nao-existe.db")
    page = client.get("/onde-aceita/rs")
    assert page.status_code == 200 and "Ainda não há lugares no mapa por aqui" in page.get_data(as_text=True)
    assert client.get("/").status_code == 200
    assert client.get("/onde-aceita/mapa.json").get_json()["pontos"] == []


def test_names_are_escaped(onde, client, tmp_path):
    conn = btcmap_sync.connect(tmp_path / "btcmap.db")
    conn.execute("UPDATE lugares SET nome='<script>x</script>', website='javascript:alert(1)' WHERE id=1")
    conn.commit()
    conn.close()
    html = client.get("/onde-aceita/sp/sao-paulo").get_data(as_text=True)
    assert "<script>x</script>" not in html and "javascript:alert" not in html
    json.loads(html.split('<script type="application/ld+json">')[3].split("</script>")[0])
    assert client.get("/onde-aceita/lugar/1.json").get_json()["site"] == ""


def test_all_states_by_default(onde, client):
    onde.app.config["ONDE_ACEITA_ESTADOS"] = []
    assert client.get("/onde-aceita/mg").status_code == 200
    assert client.get("/onde-aceita/mg/belo-horizonte").status_code == 200
    assert client.get("/onde-aceita/ac").status_code == 200
    assert "Belo Horizonte <small>MG" in client.get("/").get_data(as_text=True)


def test_merchant_block_points_to_the_node_step(onde, client):
    assert 'href="/aprenda/node-proprio"' in client.get("/onde-aceita/sp/sao-paulo").get_data(as_text=True)


# ---------- posição dos nossos cadastros (Nominatim) ----------
def test_address_queries_expand_abbreviations():
    address = "Av. Pres. Kennedy, 1622 - Água Verde, Curitiba - PR, 80220-202"
    assert btcmap_sync.city_of_address(address) == ("Curitiba", "PR")
    assert btcmap_sync.address_queries(address, "Curitiba", "PR") == [
        address, "Avenida Presidente Kennedy, 1622, Curitiba, PR"]
    back = "Fundos do Café, Av. dos Pioneiros, 00 - Centro, Carambeí - PR, 84145-000"
    assert btcmap_sync.address_queries(back, "Carambeí", "PR")[-1] == "Avenida dos Pioneiros, Carambeí, PR"


def test_geocode_ours(app_module):
    path = app_module.app.config["DB_FILE_PATH"]
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO estabelecimentos (nome, tipo, endereco, status) VALUES "
                     "('Perto', 'cafe', 'Rua A, 10 - Centro, São Paulo - SP, 01000-000', 'aprovado'),"
                     "('Longe', 'cafe', 'Rua B, 20 - Centro, Porto Alegre - RS, 90000-000', 'aprovado'),"
                     "('Pendente', 'cafe', 'Rua C, 30 - Centro, São Paulo - SP', 'pendente'),"
                     "('Sem cidade', 'cafe', 'Rua D', 'aprovado')")
    asked = []

    def fake(url):
        asked.append(url)
        return [{"lat": "-23.54", "lon": "-46.63"}]  # sempre São Paulo: longe demais de Porto Alegre

    summary = btcmap_sync.geocode_ours(path, fake, MUNICIPIOS, now=2_000_000_000, pause=0)
    assert summary == {"achados": 1, "sem_resultado": 1}
    with sqlite3.connect(path) as conn:
        rows = {r[0]: r[1:] for r in conn.execute("SELECT nome, lat, lon, geocodificado_em FROM estabelecimentos")}
    assert rows["Perto"][:2] == (-23.54, -46.63) and rows["Longe"][:2] == (None, None)
    assert rows["Pendente"] == (None, None, None) and rows["Sem cidade"] == (None, None, None)
    # quem não foi achado só tenta de novo depois de 30 dias
    asked.clear()
    btcmap_sync.geocode_ours(path, fake, MUNICIPIOS, now=2_000_000_000 + 86400, pause=0)
    assert asked == []
