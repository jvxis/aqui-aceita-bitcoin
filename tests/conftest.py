import os
import sys
import tempfile
from pathlib import Path

import pytest

# Antes de importar o app: banco, chave e logos vao para uma pasta temporaria,
# para os testes nunca tocarem no database.db nem em static/logos do repositorio.
_TMP = Path(tempfile.mkdtemp(prefix="aceita-testes-"))
os.environ["DB_FILE_PATH"] = str(_TMP / "import.db")
os.environ["UPLOAD_FOLDER"] = str(_TMP / "logos")
os.environ["FLASK_SECRET_KEY"] = "chave-so-para-testes"
os.environ.pop("CADASTRO_MODERADO", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as site  # noqa: E402


@pytest.fixture
def app_module(tmp_path):
    site.app.config["DB_FILE_PATH"] = str(tmp_path / "teste.db")
    site.app.config["UPLOAD_FOLDER"] = str(tmp_path / "logos")
    site.app.config["CADASTRO_MODERADO"] = True
    site.app.config["TESTING"] = True
    os.makedirs(site.app.config["UPLOAD_FOLDER"], exist_ok=True)
    site.init_db()
    yield site


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()
