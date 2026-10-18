"""Avisa por e-mail quando ha cadastro esperando aprovacao (pedido do Jaime em 18/10/2026).

Roda pelo aqui-aceita-bitcoin-aviso.timer, a cada 15 minutos, como webuser. Cada cadastro pendente
e avisado uma vez so (tabela avisos_pendentes); quem chega junto vai no mesmo e-mail.

Configuracao, no .env da pasta ou em /etc/aqui-aceita-bitcoin.env:
    AVISO_PARA           e-mail de quem aprova (sem ele, nada e enviado)
    AVISO_SMTP_CONFIG    JSON com smtp_user e smtp_password (padrao: o do BR⚡LN Services)

Uso:
    venv/bin/python avisar_pendentes.py run
    venv/bin/python avisar_pendentes.py dry-run   # mostra o que sairia, sem enviar nem marcar
"""

import json
import os
import smtplib
import sqlite3
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

import app as site

SMTP_CONFIG = Path(os.getenv("AVISO_SMTP_CONFIG") or "/etc/brln-services/email-login.json")


def setting(name):
    # app.py ja carrega o .env da pasta para o ambiente; a unit traz o /etc/aqui-aceita-bitcoin.env.
    return (os.getenv(name) or "").strip()


def connect():
    site.init_db()
    conn = sqlite3.connect(site.db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS avisos_pendentes (estabelecimento_id INTEGER PRIMARY KEY, avisado_em INTEGER NOT NULL)"
    )
    return conn


def novos(conn):
    return conn.execute(
        """SELECT e.* FROM estabelecimentos e
           LEFT JOIN avisos_pendentes a ON a.estabelecimento_id = e.id
           WHERE e.status = 'pendente' AND a.estabelecimento_id IS NULL
           ORDER BY e.id"""
    ).fetchall()


def descreve(row):
    pagamentos = [
        nome
        for nome, ligado in (
            ("Lightning", row["aceita_lightning"]),
            ("on-chain", row["aceita_onchain"]),
            ("contactless", row["aceita_contactless"]),
        )
        if ligado
    ]
    linhas = [
        "#%d  %s" % (row["id"], row["nome"]),
        "    Tipo: %s" % site.TYPE_LABELS.get(row["tipo"], row["tipo"]),
        "    Endereço: %s" % row["endereco"],
        "    Aceita: %s" % (", ".join(pagamentos) or "não informado"),
    ]
    for rotulo, campo in (("Site", "website"), ("E-mail", "email"), ("Telefone", "telefone"), ("Observações", "observacoes")):
        if row[campo]:
            linhas.append("    %s: %s" % (rotulo, row[campo]))
    if row["logo_filename"]:
        linhas.append("    Logo: %s/static/logos/%s" % (site.app.config["SITE_URL"], row["logo_filename"]))
    return "\n".join(linhas)


def mensagem(rows, remetente, destino):
    plural = len(rows) > 1
    msg = EmailMessage()
    msg["From"] = formataddr(("Aceita Bitcoin? / BR⚡LN", remetente))
    msg["To"] = destino
    msg["Subject"] = (
        "%d cadastros esperando aprovação no Aceita Bitcoin?" % len(rows)
        if plural
        else "Cadastro esperando aprovação no Aceita Bitcoin?: %s" % rows[0]["nome"]
    )
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    ids = " ".join(str(r["id"]) for r in rows)
    corpo = [
        "Chegou %s no aqui-aceita-bitcoin.br-ln.com, e %s só aparece%s no diretório depois de aprovado%s:"
        % ("cadastro novo" if not plural else "%d cadastros novos" % len(rows), "ele" if not plural else "eles",
           "" if not plural else "m", "" if not plural else "s"),
        "",
        "\n\n".join(descreve(r) for r in rows),
        "",
        "Aprove ou rejeite no admin do Services: https://services.br-ln.com/admin#aba-aceita",
        "",
        "Ou peça ao Claude (\"aprova o %s do Aceita Bitcoin\"), ou rode na VPS do site:" % ids,
        "    cd /home/webuser/aqui-aceita-bitcoin",
        *["    sudo -u webuser venv/bin/python gerenciar.py aprovar %d" % r["id"] for r in rows],
        "Para recusar, troque aprovar por rejeitar. Nada é apagado.",
    ]
    msg.set_content("\n".join(corpo) + "\n")
    return msg


def main():
    acao = sys.argv[1] if len(sys.argv) == 2 else ""
    if acao not in ("run", "dry-run"):
        raise SystemExit("Uso: run|dry-run")
    destino = setting("AVISO_PARA")
    with connect() as conn:
        rows = novos(conn)
        if not rows:
            print("nada novo para aprovar")
            return
        if acao == "dry-run" or not destino:
            print("sairia aviso de %s: %s" % (len(rows), ", ".join("#%d" % r["id"] for r in rows)))
            if not destino:
                print("AVISO_PARA não configurado: nada enviado")
            return
        config = json.loads(SMTP_CONFIG.read_text(encoding="utf-8"))
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=20)
        try:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(config["smtp_user"], config["smtp_password"])
            if smtp.send_message(mensagem(rows, config["smtp_user"], destino)):
                raise SystemExit("o servidor recusou o destinatário")
        finally:
            smtp.quit()
        agora = int(time.time())
        conn.executemany(
            "INSERT OR IGNORE INTO avisos_pendentes (estabelecimento_id, avisado_em) VALUES (?, ?)",
            [(r["id"], agora) for r in rows],
        )
        conn.commit()
        print("aviso enviado: %s" % ", ".join("#%d" % r["id"] for r in rows))


if __name__ == "__main__":
    main()
