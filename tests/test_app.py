import io
import os
import sqlite3
import stat
import sys
import time

import pytest

import gerenciar

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

PAGINAS = [
    "/",
    "/aprenda",
    "/aprenda/por-que-aceitar",
    "/aprenda/como-comecar",
    "/aprenda/como-receber",
    "/aprenda/seguranca",
    "/carteiras",
    "/cadastro",
    "/materiais",
    "/sobre",
    "/estabelecimentos",
    "/robots.txt",
    "/sitemap.xml",
]


def make_token(site, age_seconds=10):
    payload = {"form": "cadastro", "issued_at": int(time.time()) - age_seconds}
    return site.get_submission_serializer().dumps(payload)


def form_data(site, **overrides):
    data = {
        "submission_token": make_token(site),
        "nome": "Padaria do Teste",
        "tipo": "bakery",
        "endereco": "Rua A, 10, Carambeí, PR",
        "email": "dono@padaria.com",
        "telefone": "+55 42 99999-0000",
        "website": "https://padaria.example",
        "data_verificacao": "",
        "aceita_lightning": "true",
        "aceita_onchain": "false",
        "aceita_contactless": "false",
        "observacoes": "",
    }
    data.update(overrides)
    return data


def post_cadastro(client, data, **kwargs):
    return client.post("/api/estabelecimentos", data=data, content_type="multipart/form-data", **kwargs)


def public_names(client):
    return [item["name"] for item in client.get("/api/estabelecimentos").get_json()]


# Paginas -------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGINAS)
def test_paginas_principais_respondem(client, path):
    response = client.get(path)
    assert response.status_code == 200


def test_artigo_inexistente_da_404(client):
    response = client.get("/aprenda/nao-existe")
    assert response.status_code == 404
    assert "Página não encontrada" in response.get_data(as_text=True)


def test_api_404_responde_json(client):
    response = client.get("/api/nada")
    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_redirecionamentos_antigos(client):
    response = client.get("/carteiras.html?x=1")
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/carteiras?x=1")


def test_marca_com_raio_e_links_com_origem(client):
    html = client.get("/").get_data(as_text=True)
    assert "BR⚡LN" in html
    assert "BRLN Club" not in html
    assert "https://br-ln.com/faucet/?origem=aceita" in html


def test_trilha_tem_navegacao_entre_passos(client):
    html = client.get("/aprenda/como-comecar").get_data(as_text=True)
    assert "/aprenda/por-que-aceitar" in html
    assert "/carteiras" in html


def test_sitemap_lista_a_trilha(client):
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    for path in ("/aprenda/como-receber", "/carteiras", "/estabelecimentos"):
        assert path in xml


def test_canonical_usa_site_url_sem_query(client):
    html = client.get("/cadastro?nome=abc").get_data(as_text=True)
    assert '<link rel="canonical" href="https://aceita-bitcoin.br-ln.com/cadastro">' in html


def test_cabecalhos_de_seguranca(client):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_paginas_nao_tem_script_inline_executavel(client):
    # A CSP proibe script inline; so blocos de dados (JSON) podem ficar no HTML.
    for path in PAGINAS[:11]:
        html = client.get(path).get_data(as_text=True)
        for chunk in html.split("<script")[1:]:
            tag = chunk.split(">", 1)[0]
            assert "src=" in tag or "application/ld+json" in tag or "application/json" in tag, (path, tag)


def test_prefill_do_cadastro_e_escapado(client):
    html = client.get("/cadastro?nome=%3Cscript%3Ealert(1)%3C/script%3E").get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)" in html


def test_brln_url():
    import app as site

    assert site.brln_url() == "https://br-ln.com/?origem=aceita"
    assert site.brln_url("guias/") == "https://br-ln.com/guias/?origem=aceita"
    assert site.brln_url("ferramentas/#lnaddress") == "https://br-ln.com/ferramentas/?origem=aceita#lnaddress"


# Cadastro ------------------------------------------------------------------


def test_cadastro_fica_pendente_ate_aprovar(client, app_module):
    response = post_cadastro(client, form_data(app_module))
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["status"] == "pendente"
    assert "Padaria do Teste" not in public_names(client)

    with sqlite3.connect(app_module.db_path()) as conn:
        cadastro_id = conn.execute("SELECT id FROM estabelecimentos").fetchone()[0]
    assert gerenciar.set_status(cadastro_id, "aprovado") == 0
    assert "Padaria do Teste" in public_names(client)


def test_api_publica_nao_expoe_contato(client, app_module):
    app_module.app.config["CADASTRO_MODERADO"] = False
    post_cadastro(client, form_data(app_module))
    item = client.get("/api/estabelecimentos").get_json()[0]
    assert "email" not in item and "phone" not in item and "notes" not in item


def test_sem_moderacao_publica_direto(client, app_module):
    app_module.app.config["CADASTRO_MODERADO"] = False
    response = post_cadastro(client, form_data(app_module))
    assert response.get_json()["status"] == "aprovado"
    assert "Padaria do Teste" in public_names(client)


def test_token_ausente_ou_recente_e_recusado(client, app_module):
    assert post_cadastro(client, form_data(app_module, submission_token="")).status_code == 400
    novo = make_token(app_module, age_seconds=0)
    response = post_cadastro(client, form_data(app_module, submission_token=novo))
    assert response.status_code == 400
    assert "Espere" in response.get_json()["message"]


def test_token_com_outra_chave_e_recusado(client, app_module):
    from itsdangerous import URLSafeSerializer

    falso = URLSafeSerializer("outra-chave", salt="cadastro-antispam").dumps(
        {"form": "cadastro", "issued_at": int(time.time()) - 10}
    )
    assert post_cadastro(client, form_data(app_module, submission_token=falso)).status_code == 400


def test_honeypot_bloqueia(client, app_module):
    response = post_cadastro(client, form_data(app_module, website_confirmation="http://spam"))
    assert response.status_code == 400


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("website", "javascript:alert(1)"),
        ("website", "ftp://x.com"),
        ("tipo", "cassino"),
        ("email", "nao-e-email"),
        ("telefone", "<b>123</b>"),
        ("nome", "x" * 121),
        ("observacoes", "x" * 2001),
        ("data_verificacao", "2999-01-01"),
        ("nome", ""),
    ],
)
def test_validacao_recusa_campos_ruins(client, app_module, campo, valor):
    response = post_cadastro(client, form_data(app_module, **{campo: valor}))
    assert response.status_code == 400
    assert response.get_json()["success"] is False


def test_cadastro_duplicado(client, app_module):
    assert post_cadastro(client, form_data(app_module)).status_code == 200
    response = post_cadastro(client, form_data(app_module, nome="  padaria  do teste "))
    assert response.status_code == 409


def test_limite_de_tentativas(client, app_module):
    for _ in range(app_module.SUBMISSION_BURST_LIMIT):
        post_cadastro(client, form_data(app_module, nome=""))
    response = post_cadastro(client, form_data(app_module))
    assert response.status_code == 429


def test_logo_falsa_e_recusada(client, app_module):
    data = form_data(app_module)
    data["logo"] = (io.BytesIO(b"<html>nao sou png</html>"), "logo.png")
    response = post_cadastro(client, data)
    assert response.status_code == 400
    assert os.listdir(app_module.app.config["UPLOAD_FOLDER"]) == []


def test_logo_valida_ganha_nome_aleatorio(client, app_module):
    data = form_data(app_module)
    data["logo"] = (io.BytesIO(PNG_BYTES), "../../minha logo.png")
    response = post_cadastro(client, data)
    assert response.status_code == 200
    arquivos = os.listdir(app_module.app.config["UPLOAD_FOLDER"])
    assert len(arquivos) == 1
    assert arquivos[0].endswith(".png") and len(arquivos[0]) == 36


def test_site_antigo_com_javascript_nao_vira_link(client, app_module):
    with sqlite3.connect(app_module.db_path()) as conn:
        conn.execute(
            "INSERT INTO estabelecimentos (nome, tipo, endereco, website) VALUES (?, ?, ?, ?)",
            ("Antigo", "shop", "Rua B, Curitiba, PR", "javascript:alert(1)"),
        )
    item = next(i for i in client.get("/api/estabelecimentos").get_json() if i["name"] == "Antigo")
    assert item["website"] == ""


# Banco e configuracao ------------------------------------------------------

ESQUEMA_ANTIGO = """
CREATE TABLE estabelecimentos (
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
);
"""


def test_migracao_aditiva_e_idempotente(app_module, tmp_path):
    antigo = tmp_path / "antigo.db"
    with sqlite3.connect(antigo) as conn:
        conn.executescript(ESQUEMA_ANTIGO)
        conn.execute(
            "INSERT INTO estabelecimentos (nome, tipo, endereco, aceita_lightning) VALUES (?, ?, ?, ?)",
            ("Café Antigo", "cafe", "Rua C, Ponta Grossa, PR", 1),
        )

    app_module.app.config["DB_FILE_PATH"] = str(antigo)
    app_module.init_db()
    app_module.init_db()

    with sqlite3.connect(antigo) as conn:
        rows = conn.execute("SELECT nome, status FROM estabelecimentos").fetchall()
        colunas = {row[1] for row in conn.execute("PRAGMA table_info(estabelecimentos)")}
    assert rows == [("Café Antigo", "aprovado")]
    assert {"status", "criado_em", "revisado_em"} <= colunas

    with app_module.app.test_request_context():
        assert [e["name"] for e in app_module.fetch_establishments()] == ["Café Antigo"]


def test_chave_secreta_vem_do_arquivo_e_persiste(app_module, tmp_path, monkeypatch):
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    arquivo = tmp_path / "instancia" / "secret_key"
    monkeypatch.setenv("SECRET_KEY_FILE", str(arquivo))

    primeira = app_module.load_secret_key()
    segunda = app_module.load_secret_key()
    assert primeira == segunda
    assert len(primeira) >= 32
    assert arquivo.read_text().strip() == primeira
    if sys.platform != "win32":
        assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600


def test_chave_secreta_do_ambiente_tem_prioridade(app_module, monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "da-variavel")
    assert app_module.load_secret_key() == "da-variavel"


def test_gerenciar_lista_pendentes(client, app_module, capsys):
    post_cadastro(client, form_data(app_module))
    assert gerenciar.main(["pendentes"]) == 0
    saida = capsys.readouterr().out
    assert "Padaria do Teste" in saida and "[pendente]" in saida


def test_analytics_only_through_consent_script(client):
    response = client.get("/")
    html = response.get_data(as_text=True)
    csp = response.headers["Content-Security-Policy"]
    assert 'data-analytics-id="G-ZQVS83N61C"' in html
    assert 'id="consent-banner"' in html
    # A tag do Google nunca vem no HTML: só o privacy.js a carrega, depois do "Aceitar".
    assert "googletagmanager.com/gtag/js" not in html
    assert "script-src 'self' https://www.googletagmanager.com" in csp
    assert "https://*.google-analytics.com" in csp
