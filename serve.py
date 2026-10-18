"""Servidor de producao (waitress) do Aceita Bitcoin?.

    venv/bin/python serve.py

Variaveis (no ambiente ou no .env): FLASK_HOST (padrao 127.0.0.1), PORT ou
FLASK_PORT (padrao 5000), WAITRESS_THREADS (padrao 8).
"""

import os

from waitress import serve

from app import PORT, app


def main():
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT") or PORT)
    threads = int(os.getenv("WAITRESS_THREADS") or 8)
    # O nginx na frente cuida do TLS e repassa X-Forwarded-*; quem le esses
    # cabecalhos e o ProxyFix do app, por isso o waitress nao mexe neles.
    serve(app, host=host, port=port, threads=threads, ident="aceita-bitcoin")


if __name__ == "__main__":
    main()
