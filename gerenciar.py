"""Moderacao dos cadastros pela linha de comando.

Rode com o mesmo usuario e o mesmo .env do servico, de dentro da pasta do app:

    sudo -u webuser venv/bin/python gerenciar.py pendentes
    sudo -u webuser venv/bin/python gerenciar.py ver 12
    sudo -u webuser venv/bin/python gerenciar.py aprovar 12
    sudo -u webuser venv/bin/python gerenciar.py rejeitar 12
    sudo -u webuser venv/bin/python gerenciar.py listar --todos

Nada e apagado: rejeitar so tira o cadastro do diretorio publico, e aprovar de novo
o devolve.
"""

import argparse
from datetime import datetime
import sqlite3
import sys
import time

import app as site


def connect():
    site.init_db()
    conn = sqlite3.connect(site.db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def format_ts(value):
    if not value:
        return "-"
    return datetime.fromtimestamp(int(value)).strftime("%d/%m/%Y %H:%M")


def print_rows(rows):
    if not rows:
        print("Nenhum cadastro.")
        return
    for row in rows:
        pagamentos = [
            nome
            for nome, ligado in (
                ("Lightning", row["aceita_lightning"]),
                ("on-chain", row["aceita_onchain"]),
                ("contactless", row["aceita_contactless"]),
            )
            if ligado
        ]
        print(
            f"#{row['id']:<5} [{row['status']}] {row['nome']} | {site.TYPE_LABELS.get(row['tipo'], row['tipo'])} | "
            f"{row['endereco']} | {', '.join(pagamentos) or 'sem forma de pagamento'} | recebido {format_ts(row['criado_em'])}"
        )


def cmd_listar(args):
    with connect() as conn:
        if args.todos:
            rows = conn.execute("SELECT * FROM estabelecimentos ORDER BY id DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM estabelecimentos WHERE status = ? ORDER BY id DESC", (args.status,)
            ).fetchall()
    print_rows(rows)


def cmd_pendentes(_args):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM estabelecimentos WHERE status = ? ORDER BY id", (site.STATUS_PENDENTE,)
        ).fetchall()
    print_rows(rows)


def cmd_ver(args):
    with connect() as conn:
        row = conn.execute("SELECT * FROM estabelecimentos WHERE id = ?", (args.id,)).fetchone()
    if not row:
        print(f"Cadastro #{args.id} não existe.")
        return 1
    for key in row.keys():
        value = row[key]
        if key in {"criado_em", "revisado_em"}:
            value = format_ts(value)
        print(f"{key:>20}: {value if value not in (None, '') else '-'}")
    return 0


def set_status(cadastro_id, status):
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE estabelecimentos SET status = ?, revisado_em = ? WHERE id = ?",
            (status, int(time.time()), cadastro_id),
        )
        conn.commit()
    if cursor.rowcount == 0:
        print(f"Cadastro #{cadastro_id} não existe.")
        return 1
    print(f"Cadastro #{cadastro_id} agora está {status}.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Moderação dos cadastros do Aceita Bitcoin?")
    sub = parser.add_subparsers(dest="comando", required=True)

    sub.add_parser("pendentes", help="lista os cadastros esperando revisão").set_defaults(func=cmd_pendentes)

    listar = sub.add_parser("listar", help="lista cadastros por status")
    listar.add_argument("--status", default=site.STATUS_APROVADO, choices=sorted(site.STATUS_VALUES))
    listar.add_argument("--todos", action="store_true", help="todos os status")
    listar.set_defaults(func=cmd_listar)

    ver = sub.add_parser("ver", help="mostra todos os campos de um cadastro")
    ver.add_argument("id", type=int)
    ver.set_defaults(func=cmd_ver)

    for nome, status, ajuda in (
        ("aprovar", site.STATUS_APROVADO, "publica no diretório"),
        ("rejeitar", site.STATUS_REJEITADO, "tira do diretório sem apagar"),
        ("pendente", site.STATUS_PENDENTE, "volta para a fila de revisão"),
    ):
        parser_status = sub.add_parser(nome, help=ajuda)
        parser_status.add_argument("id", type=int)
        parser_status.set_defaults(func=lambda args, status=status: set_status(args.id, status))

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
