from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4

import dotenv
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
dotenv.load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("aceita-bitcoin")


def resolve_path(value, default):
    """Caminho relativo vale a partir da pasta do app, nao do diretorio corrente."""
    path = Path(value or default).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


def env_flag(name, default):
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def load_secret_key():
    """Le a chave do ambiente ou de um arquivo fora do git; cria o arquivo na primeira vez.

    Ordem: FLASK_SECRET_KEY / SECRET_KEY no ambiente (ou no .env), depois o arquivo
    SECRET_KEY_FILE (padrao instance/secret_key, ignorado pelo git). Se nada disso
    existir, gera uma chave aleatoria e grava no arquivo com permissao 600, para que
    os formularios abertos continuem valendo depois de um reinicio do servico.
    """
    from_env = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")
    if from_env:
        return from_env

    key_path = resolve_path(os.getenv("SECRET_KEY_FILE"), "instance/secret_key")
    try:
        if key_path.exists():
            stored = key_path.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        key_path.parent.mkdir(parents=True, exist_ok=True)
        new_key = secrets.token_hex(32)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_key)
        return new_key
    except OSError:
        logger.warning("Nao foi possivel ler nem gravar %s; usando chave temporaria.", key_path)
        return secrets.token_hex(32)


app = Flask(__name__)
app.json.sort_keys = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SECRET_KEY"] = load_secret_key()
app.config["DB_FILE_PATH"] = str(resolve_path(os.getenv("DB_FILE_PATH"), "database.db"))
# Os logos sao servidos em /static/logos: so mude UPLOAD_FOLDER nos testes.
app.config["UPLOAD_FOLDER"] = str(resolve_path(os.getenv("UPLOAD_FOLDER"), "static/logos"))
app.config["CADASTRO_MODERADO"] = env_flag("CADASTRO_MODERADO", True)
app.config["SITE_URL"] = (os.getenv("SITE_URL") or "https://aqui-aceita-bitcoin.br-ln.com").rstrip("/")

TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS") or 1)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXY_HOPS, x_proto=1, x_host=1)

PORT = int(os.getenv("PORT") or 5000)
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

BRLN_SITE = "https://br-ln.com"
BRLN_ORIGIN_PARAM = "origem=aceita"

STATUS_PENDENTE = "pendente"
STATUS_APROVADO = "aprovado"
STATUS_REJEITADO = "rejeitado"
STATUS_VALUES = {STATUS_PENDENTE, STATUS_APROVADO, STATUS_REJEITADO}

TYPE_LABELS = {
    "restaurant": "Restaurante",
    "cafe": "Café",
    "bar": "Bar",
    "hotel": "Hotel",
    "shop": "Loja",
    "supermarket": "Supermercado",
    "bakery": "Padaria",
    "pharmacy": "Farmácia",
    "healthcare": "Saúde",
    "rental": "Serviços e locação",
    "education": "Educação",
    "other": "Outro",
}

SUBMISSION_TYPE_OPTIONS = [
    {"value": value, "label": TYPE_LABELS[value]}
    for value in (
        "restaurant",
        "cafe",
        "bar",
        "hotel",
        "shop",
        "supermarket",
        "bakery",
        "pharmacy",
        "healthcare",
        "rental",
        "other",
    )
]
SUBMISSION_TYPE_VALUES = {item["value"] for item in SUBMISSION_TYPE_OPTIONS}
ALLOWED_WEBSITE_SCHEMES = {"http", "https"}
SUBMISSION_TOKEN_MIN_AGE_SECONDS = 3
SUBMISSION_TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60
SUBMISSION_BURST_LIMIT = 6
SUBMISSION_BURST_WINDOW_SECONDS = 10 * 60
SUBMISSION_DAILY_LIMIT = 30

FIELD_LIMITS = {
    "nome": 120,
    "endereco": 240,
    "email": 160,
    "telefone": 40,
    "website": 300,
    "observacoes": 2000,
}
FIELD_LABELS = {
    "nome": "Nome do estabelecimento",
    "tipo": "Tipo",
    "endereco": "Endereço",
    "email": "E-mail",
    "telefone": "Telefone",
    "website": "Website",
    "observacoes": "Observações",
}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[0-9+()\-.\s]{8,40}$")

SITE_NAV = [
    {"endpoint": "serve_index", "label": "Início"},
    {"endpoint": "serve_aprenda", "label": "Aprenda"},
    {"endpoint": "serve_carteiras", "label": "Carteiras"},
    {"endpoint": "serve_estabelecimentos", "label": "Onde aceita"},
    {"endpoint": "serve_materiais", "label": "Materiais"},
    {"endpoint": "serve_sobre", "label": "Projeto"},
]

# Trilha educativa, na ordem em que faz sentido ler.
LEARN_TRAIL = [
    {
        "slug": "por-que-aceitar",
        "endpoint": "serve_aprenda_artigo",
        "title": "Por que aceitar bitcoin",
        "kicker": "Passo 1",
        "summary": "O que o comércio ganha com Lightning, o que muda no caixa e os limites que vale conhecer antes.",
        "description": "Vantagens e limites de aceitar bitcoin e Lightning no comércio, explicados sem promessa de ganho.",
    },
    {
        "slug": "como-comecar",
        "endpoint": "serve_aprenda_artigo",
        "title": "Como começar",
        "kicker": "Passo 2",
        "summary": "Do primeiro teste com poucos sats até a primeira venda real, em oito passos.",
        "description": "Passo a passo para o comerciante começar a receber bitcoin: carteira, teste, caixa, equipe e divulgação.",
    },
    {
        "slug": None,
        "endpoint": "serve_carteiras",
        "title": "Escolher a carteira",
        "kicker": "Passo 3",
        "summary": "Custódia de terceiros ou autocustódia: o que cada uma pede de você e quais carteiras usar.",
        "description": "",
    },
    {
        "slug": "como-receber",
        "endpoint": "serve_aprenda_artigo",
        "title": "Como receber no caixa",
        "kicker": "Passo 4",
        "summary": "Fatura, endereço Lightning, QR fixo e on-chain: quando usar cada um e como conferir o pagamento.",
        "description": "Formas de cobrar em bitcoin no caixa e como conferir se o pagamento chegou de verdade.",
    },
    {
        "slug": "seguranca",
        "endpoint": "serve_aprenda_artigo",
        "title": "Cuidados e segurança",
        "kicker": "Passo 5",
        "summary": "Frase de recuperação, golpes comuns no balcão, celular do caixa e o que fazer com o que entra.",
        "description": "Como proteger a carteira do comércio, evitar golpes comuns e cuidar do que é recebido em bitcoin.",
    },
]
LEARN_ARTICLES = {item["slug"]: item for item in LEARN_TRAIL if item["slug"]}

SOCIAL_LINKS = [
    {"label": "Instagram", "url": "https://www.instagram.com/brln_club", "icon": "fa-brands fa-instagram"},
    {"label": "X", "url": "https://x.com/brln_club", "icon": "fa-brands fa-x-twitter"},
    {"label": "Services", "url": "https://services.br-ln.com/", "icon": "fa-solid fa-arrow-up-right-from-square"},
]


def brln_url(path=""):
    """Link para br-ln.com com a marca de origem, para medir quem veio daqui."""
    path = path.lstrip("/")
    fragment = ""
    if "#" in path:
        path, fragment = path.split("#", 1)
        fragment = f"#{fragment}"
    separator = "&" if "?" in path else "?"
    return f"{BRLN_SITE}/{path}{separator}{BRLN_ORIGIN_PARAM}{fragment}"


FOOTER_LINKS = [
    {"label": "BR⚡LN Club", "url": brln_url()},
    {"label": "Guias do BR⚡LN", "url": brln_url("guias/")},
    {"label": "Ferramentas grátis", "url": brln_url("ferramentas/")},
    {"label": "Faucet: primeiros sats", "url": brln_url("faucet/")},
    {"label": "BTC Map", "url": "https://btcmap.org/"},
    {"label": "Status dos serviços", "url": "https://uptime.br-ln.com/status/brln"},
]

PILOT_ESTABLISHMENTS = [
    {
        "name": "Frederica's Koffiehuis",
        "type": "cafe",
        "type_label": "Café",
        "address": "Avenida dos Pioneiros, 1010, Carambeí, PR",
        "city_label": "Carambeí, PR",
        "excerpt": "Caso piloto do projeto, com ativação no balcão e a equipe treinada para receber via Lightning.",
        "accepts_lightning": True,
        "accepts_onchain": True,
        "accepts_contactless": False,
        "logo_url": "/static/logos/1748452505.131881_logo_fredericas.png",
        "website": "",
    },
    {
        "name": "DeltaD Engenharia e Locações",
        "type": "rental",
        "type_label": "Serviços e locação",
        "address": "Carambeí, PR",
        "city_label": "Carambeí, PR",
        "excerpt": "Empresa atendida pelo projeto, mostrando que bitcoin também serve para serviços, não só para o varejo.",
        "accepts_lightning": True,
        "accepts_onchain": True,
        "accepts_contactless": False,
        "logo_url": "/static/logos/1748453054.60404_DeltaD%20-%20logomarca.gif",
        "website": "",
    },
    {
        "name": "BR⚡LN Club",
        "type": "education",
        "type_label": "Comunidade e suporte",
        "address": "Operação distribuída com base no Brasil",
        "city_label": "Brasil",
        "excerpt": "Comunidade brasileira de Bitcoin e Lightning que dá suporte, materiais e guias ao Aceita Bitcoin?.",
        "accepts_lightning": True,
        "accepts_onchain": True,
        "accepts_contactless": False,
        "logo_url": "/static/logos/1748453885.53895_BR_LN_whr1.png",
        "website": "https://services.br-ln.com/",
    },
]

# Os pilotos (Frederica's e DeltaD) ja estao cadastrados no banco de producao; soma-los ao diretorio
# os mostrava duas vezes e inflava a contagem de locais (18/10/2026). O diretorio e so o banco.
DIRECTORY_PILOTS = []

WALLETS = [
    {
        "name": "Blink",
        "url": "https://www.blink.sv/",
        "custody": "Custódia de terceiros",
        "custody_kind": "custodial",
        "badge": "Começo rápido",
        "summary": "Carteira Lightning simples, com endereço Lightning e modo de ponto de venda para cobrar no balcão.",
        "fit": "Para começar rápido e treinar a equipe sem se preocupar com canais.",
        "highlights": [
            "A empresa guarda os fundos: prático, mas exige confiar nela",
            "Tem endereço Lightning próprio para receber",
            "Confira se o app atende o Brasil antes de indicar",
        ],
    },
    {
        "name": "Coinos",
        "url": "https://coinos.io/",
        "custody": "Custódia de terceiros",
        "custody_kind": "custodial",
        "badge": "Funciona no navegador",
        "summary": "Carteira que roda no navegador, sem instalar nada, com tela de cobrança e endereço Lightning.",
        "fit": "Para um tablet ou computador no caixa, ou para quem não quer instalar app.",
        "highlights": [
            "A empresa guarda os fundos: deixe só o capital de giro",
            "Tela de ponto de venda que gera a cobrança em reais",
            "Endereço Lightning no formato nome@coinos.io",
        ],
    },
    {
        "name": "Phoenix",
        "url": "https://phoenix.acinq.co/",
        "custody": "Autocustódia",
        "custody_kind": "self",
        "badge": "Mais soberania",
        "summary": "Carteira Lightning em que só você tem as chaves, com a parte técnica dos canais automatizada.",
        "fit": "Para quem quer o controle dos próprios bitcoins e topa cuidar do backup.",
        "highlights": [
            "Só você tem as chaves: guarde bem as 12 palavras",
            "Cobra uma taxa quando precisa ampliar o canal para receber; veja a tabela no site",
            "Bom passo seguinte depois de testar com uma carteira simples",
        ],
    },
    {
        "name": "Strike",
        "url": "https://strike.me/",
        "custody": "Custódia de terceiros",
        "custody_kind": "custodial",
        "badge": "Ecossistema amplo",
        "summary": "Plataforma de bitcoin com pagamentos Lightning e outros serviços além da carteira.",
        "fit": "Para quem quer uma conta mais completa, se estiver disponível na sua região.",
        "highlights": [
            "A empresa guarda os fundos e pede cadastro com documento",
            "Recursos e disponibilidade mudam conforme o país",
            "Confira se atende o Brasil antes de indicar",
        ],
    },
]

MATERIAL_DOWNLOADS = [
    {
        "title": "Adesivo horizontal de vitrine",
        "format": "PNG",
        "size": "30 x 7,75 cm",
        "file": "downloads/materiais_adesivo_AceitaBTC.png",
        "preview": "img/logo.png",
        "description": "Peça principal para comunicar, na entrada, que o local aceita bitcoin.",
    },
    {
        "title": "Adesivo de entrada",
        "format": "PNG",
        "size": "30 x 7,75 cm",
        "file": "downloads/material_adesivo_aceit_btc_1200_310.png",
        "preview": "img/material_adesivo_aceit_btc_1200_310.png",
        "description": "Versão pronta para impressão e aplicação em porta, caixa ou vitrine.",
    },
    {
        "title": "Folder Aceita Bitcoin?",
        "format": "PDF",
        "size": "1000 x 2400 px",
        "file": "downloads/material_folder_AceitaBTC01_1000x2400.pdf",
        "preview": "img/material_folder_capa_AceitaBTC01__1000x500.png",
        "description": "Material de apoio para explicar a proposta do projeto e os primeiros passos.",
    },
]

RESOURCE_LIBRARY = [
    {
        "title": "Apostila: Bitcoin para comerciantes",
        "format": "PDF",
        "file": "downloads/material_apostila_bitcoin_para_comerciantes.pdf",
        "description": "Guia prático para apresentar bitcoin como meio de pagamento no comércio local.",
    },
    {
        "title": "Manual: Lightning Network",
        "format": "PDF",
        "file": "downloads/material_manual_lightning_network.pdf",
        "description": "Material de apoio para explicar como funcionam os recebimentos instantâneos com Lightning.",
    },
]

WHATSAPP_MESSAGES = [
    {
        "id": "whatsapp-intro",
        "title": "Abordagem inicial",
        "content": """Olá! Tudo bem?

Estou organizando uma iniciativa local para ajudar estabelecimentos a aceitarem bitcoin de forma simples, principalmente pela Lightning.

A ideia é oferecer apoio no cadastro, orientação de carteira e material visual para comunicar que o local já recebe esse tipo de pagamento.

Se fizer sentido para você, posso explicar em poucos minutos como funciona na prática.""",
    },
    {
        "id": "whatsapp-benefits",
        "title": "Mensagem de benefícios",
        "content": """Aceitar bitcoin pode trazer alguns ganhos práticos:

- clientes da comunidade que procuram lugares que aceitam
- pagamento que chega em segundos pela Lightning, sem estorno
- sem maquininha nem mensalidade: basta um celular
- presença no mapa e nos materiais da iniciativa local

Se quiser, eu ajudo no primeiro setup e numa venda de teste.""",
    },
    {
        "id": "whatsapp-visit",
        "title": "Convite para visita",
        "content": """Posso passar no estabelecimento para uma ativação rápida:

1. configurar uma carteira
2. revisar como cobrar no caixa
3. entregar o material visual
4. fazer uma compra de teste com bitcoin

Se topar, me diga um horário bom e eu organizo contigo.""",
    },
]

TIMELINE = [
    {
        "period": "Janeiro de 2025",
        "title": "Estrutura inicial do projeto",
        "text": "A iniciativa começou como uma frente do BR⚡LN para apoiar a adoção de bitcoin no comércio, com foco na prática.",
    },
    {
        "period": "Abril de 2025",
        "title": "Primeiros pilotos em Carambeí",
        "text": "O projeto validou a abordagem, o material visual e a ativação presencial em estabelecimentos locais.",
    },
    {
        "period": "Maio de 2025",
        "title": "Primeira versão pública do site",
        "text": "Foi ao ar a primeira versão, com cadastro básico, páginas informativas e materiais de apoio.",
    },
    {
        "period": "Março de 2026",
        "title": "Nova fase digital do projeto",
        "text": "O site ganhou navegação mais clara e uma base preparada para crescer.",
    },
    {
        "period": "Outubro de 2026",
        "title": "Site educativo",
        "text": "Entrou a trilha Aprenda, com o passo a passo para comerciantes e pessoas comuns aceitarem bitcoin com segurança.",
    },
]

TEAM = [
    {
        "name": "JVX",
        "role": "Coordenação e produto",
        "bio": "Responsável pela condução do projeto, curadoria do conteúdo e direção de produto da plataforma.",
    },
    {
        "name": "Dennis Verschoor",
        "role": "Expansão local",
        "bio": "Atuação direta na iniciativa em Carambeí, relacionamento com estabelecimentos e coordenação de ativações.",
    },
    {
        "name": "Rodrigo",
        "role": "Desenvolvimento",
        "bio": "Suporte técnico na implementação do site, integrações e evolução da experiência digital.",
    },
]

# Google Analytics 4 da propriedade "aceita-bitcoin" (conta BRLN). Carrega so depois que o visitante
# aceita no aviso de cookies (static/js/privacy.js); vazio desliga. A tag tinha saido numa refatoracao
# visual antiga, e a propriedade ficou sem dados ate 18/10/2026.
ANALYTICS_ID = (os.getenv("GA_MEASUREMENT_ID", "G-ZQVS83N61C") or "").strip()
GA_HOSTS = "https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://www.googletagmanager.com",
        "style-src 'self' https://fonts.googleapis.com https://cdnjs.cloudflare.com",
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com",
        "img-src 'self' data: " + GA_HOSTS,
        "frame-src https://btcmap.org",
        "connect-src 'self' " + GA_HOSTS,
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)


def db_path():
    return app.config["DB_FILE_PATH"]


def connect_db():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db(conn):
    """Migracoes aditivas e idempotentes: so acrescentam colunas e indices."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(estabelecimentos)")}
    if "status" not in columns:
        # Quem ja estava no banco continua publico, como antes da moderacao existir.
        conn.execute(
            f"ALTER TABLE estabelecimentos ADD COLUMN status TEXT NOT NULL DEFAULT '{STATUS_APROVADO}'"
        )
    if "criado_em" not in columns:
        conn.execute("ALTER TABLE estabelecimentos ADD COLUMN criado_em INTEGER")
    if "revisado_em" not in columns:
        conn.execute("ALTER TABLE estabelecimentos ADD COLUMN revisado_em INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_estabelecimentos_status ON estabelecimentos (status)")


def init_db():
    with sqlite3.connect(db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS estabelecimentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                tipo TEXT NOT NULL,
                endereco TEXT NOT NULL,
                email TEXT,
                telefone TEXT,
                website TEXT,
                observacoes TEXT,
                aceita_lightning BOOLEAN,
                aceita_onchain BOOLEAN,
                aceita_contactless BOOLEAN,
                data_verificacao TEXT,
                logo_filename TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_estabelecimentos_lookup
            ON estabelecimentos (tipo, nome, endereco)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submission_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                blocked INTEGER NOT NULL DEFAULT 0,
                reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submission_attempts_ip_created
            ON submission_attempts (ip_address, created_at)
            """
        )
        migrate_db(conn)
        conn.commit()


def allowed_logo(filename):
    if "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in ALLOWED_LOGO_EXTENSIONS


def normalize_text(value):
    return " ".join(str(value or "").split())


def normalize_optional_text(value):
    return str(value or "").strip()


def parse_checkbox_value(form_data, field_name):
    return str(form_data.get(field_name, "")).strip().lower() in {"1", "true", "on", "yes"}


def check_length(field_name, value):
    limit = FIELD_LIMITS.get(field_name)
    if limit and len(value) > limit:
        raise ValueError(f"{FIELD_LABELS[field_name]}: use no máximo {limit} caracteres.")
    return value


def validate_submission_type(raw_type):
    normalized_type = normalize_text(raw_type).lower()
    if normalized_type not in SUBMISSION_TYPE_VALUES:
        raise ValueError("Tipo de estabelecimento inválido.")
    return normalized_type


def is_safe_website(website):
    parsed = urlparse(website or "")
    return parsed.scheme.lower() in ALLOWED_WEBSITE_SCHEMES and bool(parsed.netloc)


def validate_website(raw_website):
    website = check_length("website", normalize_optional_text(raw_website))
    if not website:
        return ""
    if not is_safe_website(website):
        raise ValueError("Website inválido. Use o endereço completo, começando com http:// ou https://.")
    return website


def validate_email(raw_email):
    email = check_length("email", normalize_optional_text(raw_email))
    if email and not EMAIL_PATTERN.match(email):
        raise ValueError("E-mail inválido.")
    return email


def validate_phone(raw_phone):
    phone = check_length("telefone", normalize_text(raw_phone))
    if phone and not PHONE_PATTERN.match(phone):
        raise ValueError("Telefone inválido. Use só números, espaços e os sinais + ( ) -.")
    return phone


def validate_check_date(raw_check_date):
    check_date = normalize_optional_text(raw_check_date)
    if not check_date:
        return ""

    try:
        parsed_date = datetime.strptime(check_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Data de verificação inválida.") from exc

    # Um dia de folga: o navegador do visitante pode estar num fuso a frente do servidor.
    if parsed_date > (datetime.now() + timedelta(days=1)).date():
        raise ValueError("A data de verificação não pode estar no futuro.")
    return parsed_date.isoformat()


def detect_logo_format(file_storage):
    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header[:3] == b"\xff\xd8\xff":
        return "jpg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def validate_logo_upload(file_storage, filename):
    if not allowed_logo(filename):
        raise ValueError("Formato de logo não suportado. Use PNG, JPG, GIF ou WEBP.")

    detected_format = detect_logo_format(file_storage)
    if not detected_format:
        raise ValueError("O arquivo da logo não é uma imagem válida.")

    file_extension = filename.rsplit(".", 1)[1].lower()
    valid_extensions = {"jpg", "jpeg"} if detected_format == "jpg" else {detected_format}
    if file_extension not in valid_extensions:
        raise ValueError("A extensão da logo não corresponde ao arquivo enviado.")

    return detected_format


def extract_city_label(address):
    parts = [part.strip() for part in (address or "").split(",") if part.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[-2:])
    return address or ""


def build_establishment_base(row):
    logo_filename = row.get("logo_filename")
    logo_url = (
        url_for("static", filename=f"logos/{logo_filename}")
        if logo_filename
        else url_for("static", filename="img/logo_aceita_bitcoin_pino1_wht.png")
    )
    raw_type = row.get("tipo")
    address = row.get("endereco") or ""
    return {
        "id": row.get("id"),
        "name": row.get("nome"),
        "type": raw_type,
        "type_label": TYPE_LABELS.get(raw_type, "Outro"),
        "address": address,
        "city_label": extract_city_label(address),
        "accepts_lightning": bool(row.get("aceita_lightning")),
        "accepts_onchain": bool(row.get("aceita_onchain")),
        "accepts_contactless": bool(row.get("aceita_contactless")),
        "logo_url": logo_url,
    }


def serialize_public_establishment(row):
    establishment = build_establishment_base(row)
    website = row.get("website") or ""
    # Cadastros antigos entraram sem validacao: so http(s) vira link.
    establishment.update({"website": website if is_safe_website(website) else "", "source": "database"})
    return establishment


def serialize_internal_establishment(row):
    establishment = serialize_public_establishment(row)
    establishment.update(
        {
            "email": row.get("email"),
            "phone": row.get("telefone"),
            "notes": row.get("observacoes"),
            "check_date": row.get("data_verificacao"),
            "status": row.get("status"),
        }
    )
    return establishment


def fetch_establishments(include_private=False, status=STATUS_APROVADO):
    query = "SELECT * FROM estabelecimentos"
    params = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY id DESC"
    with connect_db() as conn:
        rows = conn.execute(query, params).fetchall()

    serializer = serialize_internal_establishment if include_private else serialize_public_establishment
    return [serializer(dict(row)) for row in rows]


def establishment_exists(conn, nome, tipo, endereco):
    row = conn.execute(
        """
        SELECT id
        FROM estabelecimentos
        WHERE lower(trim(nome)) = lower(?)
          AND lower(trim(tipo)) = lower(?)
          AND lower(trim(endereco)) = lower(?)
          AND status != ?
        LIMIT 1
        """,
        (nome.strip(), tipo.strip(), endereco.strip(), STATUS_REJEITADO),
    ).fetchone()
    return row is not None


def save_logo_upload(file_storage, safe_name):
    extension = validate_logo_upload(file_storage, safe_name)
    logo_filename = f"{uuid4().hex}.{extension}"
    logo_path = os.path.join(app.config["UPLOAD_FOLDER"], logo_filename)
    file_storage.save(logo_path)
    return logo_filename, logo_path


def get_submission_serializer():
    return URLSafeSerializer(app.config["SECRET_KEY"], salt="cadastro-antispam")


def generate_submission_token():
    payload = {
        "form": "cadastro",
        "issued_at": int(time.time()),
        "nonce": secrets.token_hex(8),
    }
    return get_submission_serializer().dumps(payload)


def validate_submission_token(token):
    if not token:
        raise ValueError("Não foi possível validar o envio. Recarregue a página e tente de novo.")

    try:
        payload = get_submission_serializer().loads(token)
    except BadSignature as exc:
        raise ValueError("Não foi possível validar o envio. Recarregue a página e tente de novo.") from exc

    if not isinstance(payload, dict) or payload.get("form") != "cadastro":
        raise ValueError("Não foi possível validar o envio. Recarregue a página e tente de novo.")

    issued_at = int(payload.get("issued_at", 0))
    age_seconds = int(time.time()) - issued_at
    if age_seconds < SUBMISSION_TOKEN_MIN_AGE_SECONDS:
        raise ValueError("Espere alguns segundos antes de enviar o cadastro.")
    if age_seconds > SUBMISSION_TOKEN_MAX_AGE_SECONDS:
        raise ValueError("O formulário expirou. Recarregue a página e tente novamente.")


def get_client_ip():
    return request.remote_addr or "unknown"


def record_submission_attempt(conn, ip_address, created_at, blocked, reason):
    conn.execute(
        """
        INSERT INTO submission_attempts (ip_address, created_at, blocked, reason)
        VALUES (?, ?, ?, ?)
        """,
        (ip_address, created_at, int(bool(blocked)), reason),
    )


def log_submission_attempt(ip_address, blocked, reason):
    with sqlite3.connect(db_path(), timeout=10) as conn:
        record_submission_attempt(conn, ip_address, int(time.time()), blocked, reason)
        conn.commit()


def prune_submission_attempts(conn, current_ts):
    conn.execute(
        "DELETE FROM submission_attempts WHERE created_at < ?",
        (current_ts - (2 * 24 * 60 * 60),),
    )


def submission_limit_message(conn, ip_address, current_ts):
    burst_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM submission_attempts
        WHERE ip_address = ?
          AND created_at >= ?
        """,
        (ip_address, current_ts - SUBMISSION_BURST_WINDOW_SECONDS),
    ).fetchone()[0]
    if burst_count >= SUBMISSION_BURST_LIMIT:
        return "Muitas tentativas em pouco tempo. Aguarde alguns minutos antes de tentar novamente."

    daily_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM submission_attempts
        WHERE ip_address = ?
          AND created_at >= ?
        """,
        (ip_address, current_ts - (24 * 60 * 60)),
    ).fetchone()[0]
    if daily_count >= SUBMISSION_DAILY_LIMIT:
        return "Limite diário de envios atingido. Tente novamente amanhã."

    return None


def directory_entries(establishments):
    return establishments + DIRECTORY_PILOTS


def build_home_stats(establishments):
    entries = directory_entries(establishments)
    mapped_cities = {entry["city_label"] for entry in entries if entry["city_label"]}
    return [
        {"value": len(entries), "label": "locais no diretório"},
        {"value": len(mapped_cities), "label": "cidades com locais"},
        {"value": len(LEARN_TRAIL), "label": "passos na trilha Aprenda"},
    ]


def canonical_url():
    return app.config["SITE_URL"] + request.path


def page_section(endpoint):
    """Marca o item do menu como ativo, inclusive nas paginas filhas da trilha Aprenda."""
    if endpoint == "serve_aprenda":
        return request.endpoint in {"serve_aprenda", "serve_aprenda_artigo"}
    return request.endpoint == endpoint


@app.after_request
def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


@app.context_processor
def inject_site_context():
    social_image = app.config["SITE_URL"] + url_for("static", filename="img/logo_aceita_bitcoin_pino1_wht.png")
    return {
        "site_nav": SITE_NAV,
        "site_social_links": SOCIAL_LINKS,
        "site_footer_links": FOOTER_LINKS,
        "submission_types": SUBMISSION_TYPE_OPTIONS,
        "learn_trail": LEARN_TRAIL,
        "current_year": datetime.now().year,
        "analytics_id": ANALYTICS_ID,
        "site_name": "Aceita Bitcoin? / BR⚡LN",
        "site_url": app.config["SITE_URL"],
        "canonical_url": canonical_url(),
        "social_image_url": social_image,
        "brln_url": brln_url,
        "page_section": page_section,
    }


@app.errorhandler(404)
def handle_not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": "Não encontrado."}), 404
    return (
        render_template(
            "erro.html",
            page="erro",
            page_title="Página não encontrada | Aceita Bitcoin?",
            page_description="A página procurada não existe ou mudou de endereço.",
            error_title="Página não encontrada",
            error_text="O endereço pode ter mudado. Comece pela trilha Aprenda ou volte ao início.",
        ),
        404,
    )


@app.errorhandler(413)
def handle_payload_too_large(_error):
    message = "O arquivo passou do limite de 5 MB."
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": message}), 413
    return message, 413


@app.errorhandler(500)
def handle_server_error(_error):
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": "Erro interno. Tente de novo em instantes."}), 500
    return (
        render_template(
            "erro.html",
            page="erro",
            page_title="Erro | Aceita Bitcoin?",
            page_description="Algo deu errado do nosso lado.",
            error_title="Algo deu errado do nosso lado",
            error_text="Tente de novo em instantes. Se continuar, avise a equipe pelo e-mail do rodapé.",
        ),
        500,
    )


@app.route("/")
def serve_index():
    establishments = fetch_establishments()
    return render_template(
        "index.html",
        page="home",
        page_title="Aceita Bitcoin? | Aprenda a receber bitcoin no seu comércio",
        page_description=(
            "Guia em português para comerciantes e pessoas comuns aceitarem bitcoin e Lightning com segurança, "
            "mais o diretório de locais que já aceitam."
        ),
        pilot_establishments=PILOT_ESTABLISHMENTS,
        recent_establishments=establishments[:3],
        project_stats=build_home_stats(establishments),
    )


@app.route("/aprenda")
def serve_aprenda():
    return render_template(
        "aprenda/index.html",
        page="aprenda",
        page_title="Aprenda a aceitar bitcoin | Aceita Bitcoin?",
        page_description=(
            "Trilha em cinco passos para aceitar bitcoin e Lightning: por que aceitar, como começar, "
            "carteiras, como receber e cuidados de segurança."
        ),
    )


@app.route("/aprenda/<slug>")
def serve_aprenda_artigo(slug):
    article = LEARN_ARTICLES.get(slug)
    if not article:
        abort(404)
    position = LEARN_TRAIL.index(article)
    previous_step = LEARN_TRAIL[position - 1] if position > 0 else None
    next_step = LEARN_TRAIL[position + 1] if position + 1 < len(LEARN_TRAIL) else None
    return render_template(
        f"aprenda/{slug}.html",
        page="aprenda",
        page_title=f"{article['title']} | Aceita Bitcoin?",
        page_description=article["description"],
        article=article,
        previous_step=previous_step,
        next_step=next_step,
    )


@app.route("/cadastro")
def serve_cadastro():
    prefill = {
        "nome": request.args.get("nome", "")[: FIELD_LIMITS["nome"]],
        "tipo": request.args.get("tipo", ""),
        "endereco": request.args.get("endereco", "")[: FIELD_LIMITS["endereco"]],
        "email": request.args.get("email", "")[: FIELD_LIMITS["email"]],
    }
    return render_template(
        "cadastro.html",
        page="cadastro",
        page_title="Cadastrar estabelecimento | Aceita Bitcoin?",
        page_description="Cadastre um estabelecimento que aceita bitcoin para ele aparecer no diretório do Aceita Bitcoin?.",
        prefill=prefill,
        field_limits=FIELD_LIMITS,
        moderated=app.config["CADASTRO_MODERADO"],
        default_check_date=datetime.now().strftime("%Y-%m-%d"),
        submission_token=generate_submission_token(),
    )


@app.route("/materiais")
def serve_materiais():
    return render_template(
        "materiais.html",
        page="materiais",
        page_title="Materiais de divulgação | Aceita Bitcoin?",
        page_description="Adesivos, folder, apostila e mensagens prontas para abordar e ativar estabelecimentos que aceitam bitcoin.",
        material_downloads=MATERIAL_DOWNLOADS,
        resource_library=RESOURCE_LIBRARY,
        whatsapp_messages=WHATSAPP_MESSAGES,
    )


@app.route("/sobre")
def serve_sobre():
    establishments = fetch_establishments()
    about_stats = [
        {"value": len(PILOT_ESTABLISHMENTS), "label": "casos piloto"},
        {"value": len(establishments), "label": "cadastros publicados"},
        {"value": len(MATERIAL_DOWNLOADS) + len(RESOURCE_LIBRARY), "label": "materiais para baixar"},
    ]
    return render_template(
        "sobre.html",
        page="sobre",
        page_title="Sobre o projeto | Aceita Bitcoin?",
        page_description="Conheça o Aceita Bitcoin?, frente do BR⚡LN que ajuda o comércio local a aceitar bitcoin.",
        about_stats=about_stats,
        timeline=TIMELINE,
        team=TEAM,
        pilot_establishments=PILOT_ESTABLISHMENTS,
    )


@app.route("/carteiras")
def serve_carteiras():
    trail_position = next(i for i, item in enumerate(LEARN_TRAIL) if item["endpoint"] == "serve_carteiras")
    return render_template(
        "carteiras.html",
        page="carteiras",
        page_title="Carteiras para receber bitcoin | Aceita Bitcoin?",
        page_description=(
            "Custódia de terceiros ou autocustódia: como escolher a carteira para receber bitcoin e Lightning no comércio."
        ),
        wallets=WALLETS,
        article=LEARN_TRAIL[trail_position],
        previous_step=LEARN_TRAIL[trail_position - 1],
        next_step=LEARN_TRAIL[trail_position + 1],
    )


@app.route("/estabelecimentos")
def serve_estabelecimentos():
    establishments = fetch_establishments()
    return render_template(
        "estabelecimentos.html",
        page="estabelecimentos",
        page_title="Onde aceita bitcoin | Aceita Bitcoin?",
        page_description="Diretório de estabelecimentos que aceitam bitcoin cadastrados no projeto Aceita Bitcoin?.",
        establishments=directory_entries(establishments),
    )


@app.route("/robots.txt")
def serve_robots():
    body = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {app.config['SITE_URL']}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def serve_sitemap():
    paths = [url_for("serve_index"), url_for("serve_aprenda")]
    paths += [url_for(item["endpoint"], **({"slug": item["slug"]} if item["slug"] else {})) for item in LEARN_TRAIL]
    paths += [url_for("serve_estabelecimentos"), url_for("serve_materiais"), url_for("serve_sobre"), url_for("serve_cadastro")]
    entries = "".join(f"<url><loc>{app.config['SITE_URL']}{path}</loc></url>" for path in paths)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    )
    return Response(body, mimetype="application/xml")


def legacy_redirect(endpoint):
    return redirect(url_for(endpoint, **request.args), code=301)


for _legacy_path, _endpoint in {
    "/index.html": "serve_index",
    "/cadastro.html": "serve_cadastro",
    "/materiais.html": "serve_materiais",
    "/sobre.html": "serve_sobre",
    "/carteiras.html": "serve_carteiras",
    "/estabelecimentos.html": "serve_estabelecimentos",
}.items():
    app.add_url_rule(
        _legacy_path,
        endpoint=f"legacy_{_endpoint}",
        view_func=lambda _endpoint=_endpoint: legacy_redirect(_endpoint),
    )


def read_submission(data):
    """Valida e normaliza os campos do cadastro. Levanta ValueError com mensagem para o usuario."""
    fields = {
        "nome": check_length("nome", normalize_text(data.get("nome"))),
        "tipo": normalize_text(data.get("tipo")).lower(),
        "endereco": check_length("endereco", normalize_text(data.get("endereco"))),
    }
    missing = [FIELD_LABELS[name] for name, value in fields.items() if not value]
    if missing:
        raise ValueError(f"Preencha os campos obrigatórios: {', '.join(missing)}.")

    fields["tipo"] = validate_submission_type(fields["tipo"])
    fields["email"] = validate_email(data.get("email"))
    fields["telefone"] = validate_phone(data.get("telefone"))
    fields["website"] = validate_website(data.get("website"))
    fields["observacoes"] = check_length("observacoes", normalize_optional_text(data.get("observacoes")))
    fields["data_verificacao"] = validate_check_date(data.get("data_verificacao"))
    fields["aceita_lightning"] = parse_checkbox_value(data, "aceita_lightning")
    fields["aceita_onchain"] = parse_checkbox_value(data, "aceita_onchain")
    fields["aceita_contactless"] = parse_checkbox_value(data, "aceita_contactless")
    return fields


@app.route("/api/estabelecimentos", methods=["POST"])
def cadastrar_estabelecimento():
    data = request.form
    arquivo = request.files.get("logo")
    client_ip = get_client_ip()
    current_ts = int(time.time())

    with sqlite3.connect(db_path(), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prune_submission_attempts(conn, current_ts)
        limit_message = submission_limit_message(conn, client_ip, current_ts)
        if limit_message:
            record_submission_attempt(conn, client_ip, current_ts, True, "rate_limit")
            conn.commit()
            return jsonify({"success": False, "message": limit_message}), 429
        conn.commit()

    if normalize_optional_text(data.get("website_confirmation")):
        log_submission_attempt(client_ip, True, "honeypot")
        return jsonify({"success": False, "message": "Não foi possível validar o envio."}), 400

    try:
        validate_submission_token(normalize_optional_text(data.get("submission_token")))
    except ValueError as exc:
        log_submission_attempt(client_ip, True, "token")
        return jsonify({"success": False, "message": str(exc)}), 400

    try:
        fields = read_submission(data)
    except ValueError as exc:
        log_submission_attempt(client_ip, True, "validation")
        return jsonify({"success": False, "message": str(exc)}), 400

    moderated = app.config["CADASTRO_MODERADO"]
    status = STATUS_PENDENTE if moderated else STATUS_APROVADO
    logo_filename = None
    logo_path = None

    try:
        with sqlite3.connect(db_path(), timeout=10) as conn:
            conn.execute("BEGIN IMMEDIATE")
            prune_submission_attempts(conn, current_ts)

            limit_message = submission_limit_message(conn, client_ip, current_ts)
            if limit_message:
                record_submission_attempt(conn, client_ip, current_ts, True, "rate_limit")
                conn.commit()
                return jsonify({"success": False, "message": limit_message}), 429

            if establishment_exists(conn, fields["nome"], fields["tipo"], fields["endereco"]):
                record_submission_attempt(conn, client_ip, current_ts, False, "duplicate")
                conn.commit()
                return (
                    jsonify(
                        {
                            "success": False,
                            "message": "Já existe um cadastro com o mesmo nome, tipo e endereço.",
                        }
                    ),
                    409,
                )

            if arquivo and arquivo.filename:
                safe_name = secure_filename(arquivo.filename)
                logo_filename, logo_path = save_logo_upload(arquivo, safe_name)

            conn.execute(
                """
                INSERT INTO estabelecimentos (
                    nome, tipo, endereco, email, telefone, website, observacoes,
                    aceita_lightning, aceita_onchain, aceita_contactless, data_verificacao, logo_filename,
                    status, criado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fields["nome"],
                    fields["tipo"],
                    fields["endereco"],
                    fields["email"],
                    fields["telefone"],
                    fields["website"],
                    fields["observacoes"],
                    fields["aceita_lightning"],
                    fields["aceita_onchain"],
                    fields["aceita_contactless"],
                    fields["data_verificacao"],
                    logo_filename,
                    status,
                    current_ts,
                ),
            )
            record_submission_attempt(conn, client_ip, current_ts, False, "accepted")
            conn.commit()
    except ValueError as exc:
        if logo_path and os.path.exists(logo_path):
            os.remove(logo_path)
        log_submission_attempt(client_ip, True, "logo_validation")
        return jsonify({"success": False, "message": str(exc)}), 400
    except sqlite3.Error:
        logger.exception("Falha ao gravar cadastro")
        if logo_path and os.path.exists(logo_path):
            os.remove(logo_path)
        log_submission_attempt(client_ip, True, "db_error")
        return jsonify({"success": False, "message": "Não foi possível salvar o cadastro agora."}), 500

    if moderated:
        message = (
            "Cadastro recebido. Ele aparece no diretório depois de uma revisão rápida da equipe, "
            "que confere se o local aceita mesmo bitcoin."
        )
    else:
        message = "Estabelecimento cadastrado com sucesso."
    return jsonify({"success": True, "message": message, "status": status})


@app.route("/api/estabelecimentos", methods=["GET"])
def listar_estabelecimentos():
    return jsonify(fetch_establishments())


init_db()


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT") or PORT)
    debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
