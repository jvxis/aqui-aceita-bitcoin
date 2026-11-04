"""Cópia dos comércios do BTC Map que ficam no Brasil, para as páginas por estado e cidade (pedido do Jaime
em 04/11/2026: ganhar as buscas "lugares que aceitam bitcoin em ...").

Os dados são do BTC Map (btcmap.org), que os tira do OpenStreetMap: licença ODbL, e a página que os mostra
cita "© colaboradores do OpenStreetMap". Ficam num banco próprio (btcmap.db), separado do database.db dos
cadastros do Aceita Bitcoin?, para o nosso cadastro não virar parte de uma base derivada do OpenStreetMap.

A sincronização segue o guia da API (docs/rest/v4/sync.md do teambtcmap/btcmap-api): na primeira vez traz
tudo; depois só o que mudou desde o último `updated_at` visto, com os removidos (`include_deleted`). O
contorno do Brasil vem da área 173 do BTC Map. O município sai da etiqueta addr:city do OpenStreetMap quando
ela bate com um nome do IBGE perto dali; sem ela, da sede de município mais próxima.

Uso: python btcmap_sync.py run      (o timer aqui-aceita-bitcoin-btcmap roda às segundas)
"""
import csv
import json
import logging
import math
import os
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
API = "https://api.btcmap.org"
# Cabeçalho HTTP só aceita latin-1: sem o raio aqui.
USER_AGENT = "aceita-bitcoin.br-ln.com (BRLN; +https://aceita-bitcoin.br-ln.com/sobre)"
BRAZIL_AREA_ID = 173
FIELDS = [
    "id", "lat", "lon", "name", "icon", "address", "opening_hours", "phone", "website", "verified_at",
    "updated_at", "created_at", "deleted_at", "osm_id", "required_app_url", "osm:addr:city",
    "osm:payment:lightning", "osm:payment:onchain", "osm:payment:lightning_contactless",
]
# Caixa que cobre o Brasil: só quem cai nela passa pelo teste do contorno, que é mais caro.
BBOX = (-34.0, 5.5, -74.5, -34.5)  # lat mín, lat máx, lon mín, lon máx
OUTLINE_MAX_AGE = 7 * 24 * 3600
# Até onde a etiqueta addr:city pode estar da sede do município com aquele nome.
CITY_NAME_MAX_KM = 80
EPOCH = "1970-01-01T00:00:00Z"

logger = logging.getLogger("btcmap-sync")


def db_path():
    value = os.getenv("BTCMAP_DB_PATH") or "btcmap.db"
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def connect(path=None):
    conn = sqlite3.connect(str(path or db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    return conn


def migrate(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS lugares (
            id INTEGER PRIMARY KEY, nome TEXT NOT NULL, icone TEXT, lat REAL NOT NULL, lon REAL NOT NULL,
            endereco TEXT, horario TEXT, telefone TEXT, website TEXT, verificado_em TEXT, atualizado_em TEXT,
            criado_em TEXT, osm_id TEXT, app_url TEXT, cidade_osm TEXT, lightning INTEGER, onchain INTEGER,
            aproximacao INTEGER, municipio_ibge INTEGER, municipio TEXT, uf TEXT)"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS lugares_uf ON lugares (uf, municipio)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT)")


def get_meta(conn, key, default=None):
    row = conn.execute("SELECT valor FROM meta WHERE chave=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn, key, value):
    conn.execute("INSERT INTO meta (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                 (key, value))


def fetch_json(url, timeout=180):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


# ---------- municípios ----------
def normalize_name(value):
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return " ".join(text.replace("-", " ").replace("'", " ").split())


def load_municipalities(path=BASE_DIR / "data" / "municipios_ibge.csv"):
    with open(path, encoding="utf-8") as handle:
        return [
            {"codigo": int(row["codigo_ibge"]), "nome": row["nome"], "uf": row["uf"], "lat": float(row["lat"]),
             "lon": float(row["lon"]), "chave": normalize_name(row["nome"])}
            for row in csv.DictReader(handle)
        ]


def km(lat1, lon1, lat2, lon2):
    dy = (lat1 - lat2) * 111.2
    dx = (lon1 - lon2) * 111.2 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


class Locator:
    def __init__(self, municipalities):
        self.all = municipalities
        self.by_name = {}
        for item in municipalities:
            self.by_name.setdefault(item["chave"], []).append(item)

    def locate(self, lat, lon, city_tag=None):
        """O município do comércio: o da etiqueta addr:city, se houver um com esse nome por perto; senão,
        a sede mais próxima. Em São Paulo a sede mais próxima erra muito (comércios da capital caem em
        Taboão, Osasco...), por isso a etiqueta vem primeiro."""
        named = self.by_name.get(normalize_name(city_tag)) if city_tag else None
        if named:
            best = min(named, key=lambda m: km(lat, lon, m["lat"], m["lon"]))
            if km(lat, lon, best["lat"], best["lon"]) <= CITY_NAME_MAX_KM:
                return best
        return min(self.all, key=lambda m: km(lat, lon, m["lat"], m["lon"]))


# ---------- contorno do Brasil ----------
def _in_ring(lon, lat, ring):
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[i - 1][0], ring[i - 1][1]
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def polygons_from_geojson(geo):
    if geo.get("type") == "FeatureCollection":
        geo = geo["features"][0]
    if geo.get("type") == "Feature":
        geo = geo["geometry"]
    return geo["coordinates"] if geo["type"] == "MultiPolygon" else [geo["coordinates"]]


def in_brazil(lat, lon, polygons):
    if not (BBOX[0] < lat < BBOX[1] and BBOX[2] < lon < BBOX[3]):
        return False
    return any(_in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, hole) for hole in poly[1:])
               for poly in polygons)


def brazil_outline(conn, fetch=fetch_json, now=None):
    now = now or time.time()
    cached, fetched_at = get_meta(conn, "contorno"), float(get_meta(conn, "contorno_em", "0"))
    if cached and now - fetched_at < OUTLINE_MAX_AGE:
        return json.loads(cached)
    area = fetch(f"{API}/v3/areas/{BRAZIL_AREA_ID}")
    geo = (area.get("tags") or {}).get("geo_json")
    if not geo:
        if cached:
            return json.loads(cached)
        raise RuntimeError("o BTC Map não mandou o contorno do Brasil")
    polygons = polygons_from_geojson(geo)
    set_meta(conn, "contorno", json.dumps(polygons))
    set_meta(conn, "contorno_em", str(now))
    return polygons


# ---------- sincronização ----------
def _flag(value):
    if value is None:
        return None
    return 1 if str(value).strip().lower() in {"yes", "on", "true", "only"} else 0


def sync(conn, fetch=fetch_json, municipalities=None, now=None):
    """Traz o que mudou no BTC Map desde a última vez. Devolve um resumo para o log."""
    polygons = brazil_outline(conn, fetch, now)
    locator = Locator(municipalities or load_municipalities())
    cursor = get_meta(conn, "cursor", EPOCH)
    params = {"fields": ",".join(FIELDS), "updated_since": cursor}
    if cursor != EPOCH:
        params["include_deleted"] = "true"
    places = fetch(f"{API}/v4/places?" + urllib.parse.urlencode(params))
    saved = removed = 0
    newest = cursor
    for place in places:
        newest = max(newest, place.get("updated_at") or newest)
        lat, lon = place.get("lat"), place.get("lon")
        if place.get("deleted_at") or lat is None or lon is None or not in_brazil(lat, lon, polygons):
            removed += conn.execute("DELETE FROM lugares WHERE id=?", (place["id"],)).rowcount
            continue
        town = locator.locate(lat, lon, place.get("osm:addr:city"))
        conn.execute(
            """INSERT OR REPLACE INTO lugares (id, nome, icone, lat, lon, endereco, horario, telefone, website,
                verificado_em, atualizado_em, criado_em, osm_id, app_url, cidade_osm, lightning, onchain,
                aproximacao, municipio_ibge, municipio, uf)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (place["id"], (place.get("name") or "").strip() or "Sem nome no mapa", place.get("icon"), lat, lon,
             place.get("address"), place.get("opening_hours"), place.get("phone"), place.get("website"),
             place.get("verified_at"), place.get("updated_at"), place.get("created_at"), place.get("osm_id"),
             place.get("required_app_url"), place.get("osm:addr:city"), _flag(place.get("osm:payment:lightning")),
             _flag(place.get("osm:payment:onchain")), _flag(place.get("osm:payment:lightning_contactless")),
             town["codigo"], town["nome"], town["uf"]),
        )
        saved += 1
    set_meta(conn, "cursor", newest)
    set_meta(conn, "sincronizado_em", datetime.fromtimestamp(now or time.time(), timezone.utc).isoformat(timespec="seconds"))
    total = conn.execute("SELECT COUNT(*) FROM lugares").fetchone()[0]
    set_meta(conn, "total", str(total))
    conn.commit()
    return {"recebidos": len(places), "gravados": saved, "removidos": removed, "total": total, "cursor": newest}


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    conn = connect()
    try:
        summary = sync(conn)
    finally:
        conn.close()
    logger.info("BTC Map: %s", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
