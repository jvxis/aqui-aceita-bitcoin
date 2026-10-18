import sqlite3

import avisar_pendentes as aviso


def insere(site, nome, status):
    with sqlite3.connect(site.db_path()) as conn:
        conn.execute(
            """INSERT INTO estabelecimentos (nome, tipo, endereco, email, aceita_lightning, aceita_onchain,
               aceita_contactless, status) VALUES (?, 'cafe', 'Rua A, 1 - Curitiba', 'dono@x.com', 1, 0, 0, ?)""",
            (nome, status),
        )


def test_avisa_cada_pendente_uma_vez(app_module):
    insere(app_module, "Café Pendente", "pendente")
    insere(app_module, "Já Aprovado", "aprovado")
    with aviso.connect() as conn:
        rows = aviso.novos(conn)
        assert [r["nome"] for r in rows] == ["Café Pendente"]
        msg = aviso.mensagem(rows, "clube@x.com", "dono@clube.com")
        corpo = msg.get_content()
        assert "Café Pendente" in msg["Subject"]
        assert "gerenciar.py aprovar %d" % rows[0]["id"] in corpo
        assert "Lightning" in corpo
        conn.execute("INSERT INTO avisos_pendentes VALUES (?, 0)", (rows[0]["id"],))
        assert aviso.novos(conn) == []


def test_varios_vao_num_email_so(app_module):
    insere(app_module, "Um", "pendente")
    insere(app_module, "Dois", "pendente")
    with aviso.connect() as conn:
        msg = aviso.mensagem(aviso.novos(conn), "clube@x.com", "dono@clube.com")
    assert msg["Subject"].startswith("2 cadastros")
