from datetime import datetime
import os
import sqlite3
import time
from urllib.parse import urlparse
from uuid import uuid4

import dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.utils import secure_filename


dotenv.load_dotenv(".env")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or os.urandom(32).hex()

DB_FILE_PATH = dotenv.get_key(".env", "DB_FILE_PATH") or "database.db"
PORT = int(dotenv.get_key(".env", "PORT") or os.getenv("PORT") or 5000)
UPLOAD_FOLDER = os.path.join(app.static_folder, "logos")
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
    {"value": "restaurant", "label": TYPE_LABELS["restaurant"]},
    {"value": "cafe", "label": TYPE_LABELS["cafe"]},
    {"value": "bar", "label": TYPE_LABELS["bar"]},
    {"value": "hotel", "label": TYPE_LABELS["hotel"]},
    {"value": "shop", "label": TYPE_LABELS["shop"]},
    {"value": "supermarket", "label": TYPE_LABELS["supermarket"]},
    {"value": "bakery", "label": TYPE_LABELS["bakery"]},
    {"value": "pharmacy", "label": TYPE_LABELS["pharmacy"]},
    {"value": "healthcare", "label": TYPE_LABELS["healthcare"]},
    {"value": "rental", "label": TYPE_LABELS["rental"]},
    {"value": "other", "label": TYPE_LABELS["other"]},
]
SUBMISSION_TYPE_VALUES = {item["value"] for item in SUBMISSION_TYPE_OPTIONS}
ALLOWED_WEBSITE_SCHEMES = {"http", "https"}
SUBMISSION_TOKEN_MIN_AGE_SECONDS = 3
SUBMISSION_TOKEN_MAX_AGE_SECONDS = 12 * 60 * 60
SUBMISSION_BURST_LIMIT = 6
SUBMISSION_BURST_WINDOW_SECONDS = 10 * 60
SUBMISSION_DAILY_LIMIT = 30

SITE_NAV = [
    {"endpoint": "serve_index", "label": "Início"},
    {"endpoint": "serve_sobre", "label": "Projeto"},
    {"endpoint": "serve_carteiras", "label": "Carteiras"},
    {"endpoint": "serve_materiais", "label": "Materiais"},
    {"endpoint": "serve_estabelecimentos", "label": "Mapa local"},
]

SOCIAL_LINKS = [
    {"label": "Instagram", "url": "https://www.instagram.com/brln_club", "icon": "fa-brands fa-instagram"},
    {"label": "X", "url": "https://x.com/brln_club", "icon": "fa-brands fa-x-twitter"},
    {"label": "Services", "url": "https://services.br-ln.com/", "icon": "fa-solid fa-arrow-up-right-from-square"},
]

FOOTER_LINKS = [
    {"label": "BRLN Club", "url": "https://br-ln.com/"},
    {"label": "Bitcoin Services", "url": "https://services.br-ln.com/"},
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
        "excerpt": "Caso piloto do projeto com ativação prática no balcão e onboarding do time para receber via Lightning.",
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
        "excerpt": "Empresa atendida pelo projeto para mostrar que Bitcoin também encaixa em serviços fora do varejo tradicional.",
        "accepts_lightning": True,
        "accepts_onchain": True,
        "accepts_contactless": False,
        "logo_url": "/static/logos/1748453054.60404_DeltaD%20-%20logomarca.gif",
        "website": "",
    },
    {
        "name": "BRLN Club",
        "type": "education",
        "type_label": "Comunidade e suporte",
        "address": "Operação distribuída com base no Brasil",
        "city_label": "Brasil",
        "excerpt": "Hub de suporte, materiais e comunidade que dá tração à frente comercial do Aceita Bitcoin?.",
        "accepts_lightning": True,
        "accepts_onchain": True,
        "accepts_contactless": False,
        "logo_url": "/static/logos/1748453885.53895_BR_LN_whr1.png",
        "website": "https://services.br-ln.com/",
    },
]

WALLETS = [
    {
        "name": "Blink",
        "url": "https://www.blink.sv/",
        "badge": "Onboarding rápido",
        "summary": "Carteira focada em simplicidade para pagamentos Lightning no balcão.",
        "fit": "Boa opção para começar rápido e treinar a equipe.",
        "highlights": [
            "Fluxo simples para receber e pagar",
            "Experiência pensada para uso diário",
            "Disponibilidade pode variar por região",
        ],
    },
    {
        "name": "Phoenix",
        "url": "https://phoenix.acinq.co/",
        "badge": "Mais soberania",
        "summary": "Carteira com foco em autocustódia e experiência moderna para Lightning.",
        "fit": "Indicado para quem quer mais controle sobre a própria custódia.",
        "highlights": [
            "Custódia própria",
            "Boa experiência para usuários mais atentos à segurança",
            "Exige entender melhor taxas e canais",
        ],
    },
    {
        "name": "Strike",
        "url": "https://strike.me/",
        "badge": "Ecossistema amplo",
        "summary": "Plataforma Bitcoin com pagamentos Lightning e recursos adicionais.",
        "fit": "Útil para quem quer uma operação mais ampla além do caixa.",
        "highlights": [
            "Lightning com experiência polida",
            "Recursos do app variam conforme o país",
            "Boa referência para operação comercial",
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
        "description": "Peça principal para comunicar aceitação de Bitcoin na entrada do local.",
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
        "description": "Guia prático para apresentar Bitcoin como meio de pagamento no comércio local.",
    },
    {
        "title": "Manual: Lightning Network",
        "format": "PDF",
        "file": "downloads/material_manual_lightning_network.pdf",
        "description": "Material de apoio para explicar como funcionam recebimentos instantâneos com Lightning.",
    },
]

WHATSAPP_MESSAGES = [
    {
        "id": "whatsapp-intro",
        "title": "Abordagem inicial",
        "content": """Olá! Tudo bem?

Estou organizando uma iniciativa local para ajudar estabelecimentos a aceitarem Bitcoin de forma simples, principalmente via Lightning.

A ideia é oferecer apoio no cadastro, orientação de carteira e material visual para comunicar que o local já recebe esse tipo de pagamento.

Se fizer sentido para você, posso explicar em poucos minutos como funciona na prática e quais são os benefícios para o seu negócio.""",
    },
    {
        "id": "whatsapp-benefits",
        "title": "Mensagem de benefícios",
        "content": """Aceitar Bitcoin pode trazer alguns ganhos imediatos:

- novas visitas de clientes da comunidade
- pagamentos instantâneos com Lightning
- posicionamento de inovação para o negócio
- presença em mapas e materiais da iniciativa local

Se quiser, eu posso ajudar no primeiro setup e na demonstração de uma venda real.""",
    },
    {
        "id": "whatsapp-visit",
        "title": "Convite para visita",
        "content": """Posso passar no estabelecimento para fazer uma ativação rápida:

1. configurar uma carteira
2. revisar como cobrar no caixa
3. entregar material visual
4. simular ou realizar a primeira compra em Bitcoin

Se topar, me diga um horário bom e eu organizo contigo.""",
    },
]

TIMELINE = [
    {
        "period": "Janeiro de 2025",
        "title": "Estrutura inicial do projeto",
        "text": "A iniciativa começou como uma frente do ecossistema BRLN para apoiar adoção comercial de Bitcoin com foco em execução prática.",
    },
    {
        "period": "Abril de 2025",
        "title": "Primeiros pilotos em Carambeí",
        "text": "O projeto validou o fluxo de onboarding, material visual e ativação presencial em estabelecimentos locais.",
    },
    {
        "period": "Maio de 2025",
        "title": "Primeira versão pública do site",
        "text": "Foi ao ar a primeira versão com cadastro básico, páginas informativas e materiais de apoio.",
    },
    {
        "period": "Março de 2026",
        "title": "Nova fase digital do projeto",
        "text": "O site entrou em uma nova fase com estrutura mais sólida, navegação mais clara e base preparada para crescer.",
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


def init_db():
    with sqlite3.connect(DB_FILE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
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
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_estabelecimentos_lookup
            ON estabelecimentos (tipo, nome, endereco)
            """
        )
        cursor.execute(
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
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_submission_attempts_ip_created
            ON submission_attempts (ip_address, created_at)
            """
        )
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


def validate_submission_type(raw_type):
    normalized_type = normalize_text(raw_type).lower()
    if normalized_type not in SUBMISSION_TYPE_VALUES:
        raise ValueError("Tipo de estabelecimento invalido.")
    return normalized_type


def validate_website(raw_website):
    website = normalize_optional_text(raw_website)
    if not website:
        return ""

    parsed = urlparse(website)
    if parsed.scheme.lower() not in ALLOWED_WEBSITE_SCHEMES or not parsed.netloc:
        raise ValueError("Website invalido. Use uma URL completa com http:// ou https://.")
    return website


def validate_check_date(raw_check_date):
    check_date = normalize_optional_text(raw_check_date)
    if not check_date:
        return ""

    try:
        parsed_date = datetime.strptime(check_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Data de verificacao invalida.") from exc

    if parsed_date > datetime.now().date():
        raise ValueError("Data de verificacao nao pode estar no futuro.")
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
        raise ValueError("Formato de logo nao suportado.")

    detected_format = detect_logo_format(file_storage)
    if not detected_format:
        raise ValueError("Arquivo de logo invalido.")

    file_extension = filename.rsplit(".", 1)[1].lower()
    valid_extensions = {"jpg", "jpeg"} if detected_format == "jpg" else {detected_format}
    if file_extension not in valid_extensions:
        raise ValueError("Extensao da logo nao corresponde ao arquivo enviado.")

    return "jpg" if detected_format == "jpg" else detected_format


def extract_city_label(address):
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 2:
        return ", ".join(parts[-2:])
    return address


def build_establishment_base(row):
    logo_filename = row.get("logo_filename") if isinstance(row, dict) else row["logo_filename"]
    logo_url = (
        url_for("static", filename=f"logos/{logo_filename}")
        if logo_filename
        else url_for("static", filename="img/logo_aceita_bitcoin_pino1_wht.png")
    )
    raw_type = row.get("tipo") if isinstance(row, dict) else row["tipo"]
    address = row.get("endereco") if isinstance(row, dict) else row["endereco"]
    return {
        "id": row.get("id") if isinstance(row, dict) else row["id"],
        "name": row.get("nome") if isinstance(row, dict) else row["nome"],
        "type": raw_type,
        "type_label": TYPE_LABELS.get(raw_type, "Outro"),
        "address": address,
        "city_label": extract_city_label(address),
        "accepts_lightning": bool(row.get("aceita_lightning") if isinstance(row, dict) else row["aceita_lightning"]),
        "accepts_onchain": bool(row.get("aceita_onchain") if isinstance(row, dict) else row["aceita_onchain"]),
        "accepts_contactless": bool(
            row.get("aceita_contactless") if isinstance(row, dict) else row["aceita_contactless"]
        ),
        "logo_url": logo_url,
    }


def serialize_public_establishment(row):
    establishment = build_establishment_base(row)
    establishment.update(
        {
            "website": row.get("website") if isinstance(row, dict) else row["website"],
            "source": "database",
        }
    )
    return establishment


def serialize_internal_establishment(row):
    establishment = serialize_public_establishment(row)
    establishment.update(
        {
            "email": row.get("email") if isinstance(row, dict) else row["email"],
            "phone": row.get("telefone") if isinstance(row, dict) else row["telefone"],
            "notes": row.get("observacoes") if isinstance(row, dict) else row["observacoes"],
            "check_date": row.get("data_verificacao") if isinstance(row, dict) else row["data_verificacao"],
        }
    )
    return establishment


def fetch_establishments(include_private=False):
    with sqlite3.connect(DB_FILE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM estabelecimentos ORDER BY id DESC").fetchall()

    serializer = serialize_internal_establishment if include_private else serialize_public_establishment
    return [serializer(dict(row)) for row in rows]


def establishment_exists(conn, nome, tipo, endereco):
    row = conn.execute(
        """
        SELECT id
        FROM estabelecimentos
        WHERE lower(trim(nome)) = ?
          AND lower(trim(tipo)) = ?
          AND lower(trim(endereco)) = ?
        LIMIT 1
        """,
        (nome.casefold(), tipo.casefold(), endereco.casefold()),
    ).fetchone()
    return row is not None


@app.errorhandler(413)
def handle_payload_too_large(_error):
    message = "Arquivo excede o limite de 5 MB."
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "message": message}), 413
    return message, 413


def save_logo_upload(file_storage, safe_name):
    extension = validate_logo_upload(file_storage, safe_name)
    logo_filename = f"{uuid4().hex}.{extension}"
    logo_path = os.path.join(UPLOAD_FOLDER, logo_filename)
    file_storage.save(logo_path)
    return logo_filename, logo_path


def get_submission_serializer():
    return URLSafeSerializer(app.config["SECRET_KEY"], salt="cadastro-antispam")


def generate_submission_token():
    payload = {
        "form": "cadastro",
        "issued_at": int(time.time()),
    }
    return get_submission_serializer().dumps(payload)


def validate_submission_token(token):
    if not token:
        raise ValueError("Nao foi possivel validar o envio.")

    try:
        payload = get_submission_serializer().loads(token)
    except BadSignature as exc:
        raise ValueError("Nao foi possivel validar o envio.") from exc

    if payload.get("form") != "cadastro":
        raise ValueError("Nao foi possivel validar o envio.")

    issued_at = int(payload.get("issued_at", 0))
    age_seconds = int(time.time()) - issued_at
    if age_seconds < SUBMISSION_TOKEN_MIN_AGE_SECONDS:
        raise ValueError("Espere alguns segundos antes de enviar o cadastro.")
    if age_seconds > SUBMISSION_TOKEN_MAX_AGE_SECONDS:
        raise ValueError("O formulario expirou. Recarregue a pagina e tente novamente.")


def get_client_ip():
    if request.access_route:
        return request.access_route[0]
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
    with sqlite3.connect(DB_FILE_PATH) as conn:
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
        return "Limite diario de envios atingido para este IP. Tente novamente mais tarde."

    return None


def build_home_stats(establishments):
    mapped_cities = {entry["city_label"] for entry in establishments if entry["city_label"]}
    return [
        {"value": len(establishments), "label": "cadastros públicos"},
        {"value": len(PILOT_ESTABLISHMENTS), "label": "casos piloto"},
        {"value": max(len(mapped_cities), 3), "label": "frentes mapeadas"},
    ]


@app.context_processor
def inject_site_context():
    social_image = url_for("static", filename="img/logo_aceita_bitcoin_pino1_wht.png", _external=True)
    return {
        "site_nav": SITE_NAV,
        "site_social_links": SOCIAL_LINKS,
        "site_footer_links": FOOTER_LINKS,
        "submission_types": SUBMISSION_TYPE_OPTIONS,
        "current_year": datetime.now().year,
        "site_name": "Aceita Bitcoin? / BRLN",
        "site_url": request.url_root.rstrip("/"),
        "social_image_url": social_image,
    }


@app.route("/")
def serve_index():
    establishments = fetch_establishments()
    featured = establishments[:3] if establishments else PILOT_ESTABLISHMENTS
    return render_template(
        "index.html",
        page="home",
        page_title="Aceita Bitcoin? | BRLN",
        page_description="Projeto para ajudar estabelecimentos e comunidades locais a ativarem pagamentos com Bitcoin e Lightning.",
        featured_establishments=featured,
        project_stats=build_home_stats(establishments),
        public_count=len(establishments),
    )


@app.route("/cadastro")
def serve_cadastro():
    prefill = {
        "nome": request.args.get("nome", ""),
        "tipo": request.args.get("tipo", ""),
        "endereco": request.args.get("endereco", ""),
        "email": request.args.get("email", ""),
    }
    return render_template(
        "cadastro.html",
        page="cadastro",
        page_title="Cadastrar estabelecimento",
        page_description="Cadastre um estabelecimento e organize a ativação local de pagamentos com Bitcoin.",
        prefill=prefill,
        default_check_date=datetime.now().strftime("%Y-%m-%d"),
        submission_token=generate_submission_token(),
    )


@app.route("/materiais")
def serve_materiais():
    return render_template(
        "materiais.html",
        page="materiais",
        page_title="Materiais de divulgação",
        page_description="Downloads, mensagens prontas e recursos para abordar, ativar e divulgar estabelecimentos que aceitam Bitcoin.",
        material_downloads=MATERIAL_DOWNLOADS,
        resource_library=RESOURCE_LIBRARY,
        whatsapp_messages=WHATSAPP_MESSAGES,
    )


@app.route("/sobre")
def serve_sobre():
    establishments = fetch_establishments()
    about_stats = [
        {"value": len(PILOT_ESTABLISHMENTS), "label": "casos piloto ativos"},
        {"value": len(establishments), "label": "cadastros públicos"},
        {"value": len(MATERIAL_DOWNLOADS) + len(RESOURCE_LIBRARY), "label": "materiais publicados"},
    ]
    return render_template(
        "sobre.html",
        page="sobre",
        page_title="Sobre o projeto",
        page_description="Conheça a proposta do Aceita Bitcoin? e como o projeto ajuda a ativar comércio local com Bitcoin.",
        about_stats=about_stats,
        timeline=TIMELINE,
        team=TEAM,
        pilot_establishments=PILOT_ESTABLISHMENTS,
    )


@app.route("/carteiras")
def serve_carteiras():
    return render_template(
        "carteiras.html",
        page="carteiras",
        page_title="Carteiras recomendadas",
        page_description="Carteiras recomendadas para começar a receber pagamentos em Bitcoin com mais segurança e clareza.",
        wallets=WALLETS,
    )


@app.route("/estabelecimentos")
def serve_estabelecimentos():
    establishments = fetch_establishments()
    return render_template(
        "estabelecimentos.html",
        page="estabelecimentos",
        page_title="Estabelecimentos cadastrados",
        page_description="Diretório público de estabelecimentos cadastrados no projeto Aceita Bitcoin?.",
        establishments=establishments,
    )


@app.route("/index.html")
def legacy_index():
    return redirect(url_for("serve_index", **request.args), code=301)


@app.route("/cadastro.html")
def legacy_cadastro():
    return redirect(url_for("serve_cadastro", **request.args), code=301)


@app.route("/materiais.html")
def legacy_materiais():
    return redirect(url_for("serve_materiais", **request.args), code=301)


@app.route("/sobre.html")
def legacy_sobre():
    return redirect(url_for("serve_sobre", **request.args), code=301)


@app.route("/carteiras.html")
def legacy_carteiras():
    return redirect(url_for("serve_carteiras", **request.args), code=301)


@app.route("/estabelecimentos.html")
def legacy_estabelecimentos():
    return redirect(url_for("serve_estabelecimentos", **request.args), code=301)


@app.route("/api/estabelecimentos", methods=["POST"])
def cadastrar_estabelecimento():
    data = request.form
    arquivo = request.files.get("logo")

    required_fields = {
        "nome": normalize_text(data.get("nome")),
        "tipo": normalize_text(data.get("tipo")).lower(),
        "endereco": normalize_text(data.get("endereco")),
    }
    missing = [field for field, value in required_fields.items() if not value]
    if missing:
        return jsonify({"success": False, "message": f"Campos obrigatórios ausentes: {', '.join(missing)}."}), 400

    logo_filename = None
    if arquivo and arquivo.filename:
        safe_name = secure_filename(arquivo.filename)
        if not allowed_logo(safe_name):
            return jsonify({"success": False, "message": "Formato de logo não suportado."}), 400
        logo_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe_name}"
        arquivo.save(os.path.join(UPLOAD_FOLDER, logo_filename))

    with sqlite3.connect(DB_FILE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO estabelecimentos (
                nome, tipo, endereco, email, telefone, website, observacoes,
                aceita_lightning, aceita_onchain, aceita_contactless, data_verificacao, logo_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                required_fields["nome"],
                required_fields["tipo"],
                required_fields["endereco"],
                data.get("email", "").strip(),
                data.get("telefone", "").strip(),
                data.get("website", "").strip(),
                data.get("observacoes", "").strip(),
                data.get("aceita_lightning") == "true",
                data.get("aceita_onchain") == "true",
                data.get("aceita_contactless") == "true",
                data.get("data_verificacao", "").strip(),
                logo_filename,
            ),
        )
        conn.commit()

    return jsonify({"success": True, "message": "Estabelecimento cadastrado com sucesso."})


def cadastrar_estabelecimento_v2():
    data = request.form
    arquivo = request.files.get("logo")
    client_ip = get_client_ip()
    current_ts = int(time.time())

    with sqlite3.connect(DB_FILE_PATH) as conn:
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
        return jsonify({"success": False, "message": "Nao foi possivel validar o envio."}), 400

    try:
        validate_submission_token(normalize_optional_text(data.get("submission_token")))
    except ValueError as exc:
        log_submission_attempt(client_ip, True, "token")
        return jsonify({"success": False, "message": str(exc)}), 400

    required_fields = {
        "nome": normalize_text(data.get("nome")),
        "tipo": normalize_text(data.get("tipo")).lower(),
        "endereco": normalize_text(data.get("endereco")),
    }
    missing = [field for field, value in required_fields.items() if not value]
    if missing:
        log_submission_attempt(client_ip, True, "missing_fields")
        return jsonify({"success": False, "message": f"Campos obrigatorios ausentes: {', '.join(missing)}."}), 400

    try:
        validated_type = validate_submission_type(required_fields["tipo"])
        validated_website = validate_website(data.get("website"))
        validated_check_date = validate_check_date(data.get("data_verificacao"))
    except ValueError as exc:
        log_submission_attempt(client_ip, True, "validation")
        return jsonify({"success": False, "message": str(exc)}), 400

    logo_filename = None
    logo_path = None

    try:
        with sqlite3.connect(DB_FILE_PATH) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.cursor()
            prune_submission_attempts(conn, current_ts)

            limit_message = submission_limit_message(conn, client_ip, current_ts)
            if limit_message:
                record_submission_attempt(conn, client_ip, current_ts, True, "rate_limit")
                conn.commit()
                return jsonify({"success": False, "message": limit_message}), 429

            if establishment_exists(conn, required_fields["nome"], validated_type, required_fields["endereco"]):
                record_submission_attempt(conn, client_ip, current_ts, False, "duplicate")
                conn.commit()
                return jsonify(
                    {"success": False, "message": "Ja existe um cadastro com o mesmo nome, tipo e endereco."}
                ), 409

            if arquivo and arquivo.filename:
                safe_name = secure_filename(arquivo.filename)
                logo_filename, logo_path = save_logo_upload(arquivo, safe_name)

            cursor.execute(
                """
                INSERT INTO estabelecimentos (
                    nome, tipo, endereco, email, telefone, website, observacoes,
                    aceita_lightning, aceita_onchain, aceita_contactless, data_verificacao, logo_filename
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    required_fields["nome"],
                    validated_type,
                    required_fields["endereco"],
                    normalize_optional_text(data.get("email")),
                    normalize_optional_text(data.get("telefone")),
                    validated_website,
                    normalize_optional_text(data.get("observacoes")),
                    parse_checkbox_value(data, "aceita_lightning"),
                    parse_checkbox_value(data, "aceita_onchain"),
                    parse_checkbox_value(data, "aceita_contactless"),
                    validated_check_date,
                    logo_filename,
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
        if logo_path and os.path.exists(logo_path):
            os.remove(logo_path)
        log_submission_attempt(client_ip, True, "db_error")
        return jsonify({"success": False, "message": "Nao foi possivel salvar o cadastro agora."}), 500

    return jsonify({"success": True, "message": "Estabelecimento cadastrado com sucesso."})


app.view_functions["cadastrar_estabelecimento"] = cadastrar_estabelecimento_v2


@app.route("/api/estabelecimentos", methods=["GET"])
def listar_estabelecimentos():
    return jsonify(fetch_establishments())


init_db()


if __name__ == "__main__":
    host = os.getenv("FLASK_HOST", "0.0.0.0")
    port = int(os.getenv("FLASK_PORT") or PORT)
    debug = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
