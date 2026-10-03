"""Prépare un dossier de données isolé et tout neuf AVANT que les scripts de ce dossier importent server.py —
sinon ils écriraient dans le dossier réel du serveur (`server/`). Chaque script de test commence par
`import _env` avant son `import server as S` ; `python <script>` met automatiquement le dossier du script
(ici `tests/`) sur `sys.path`, donc rien d'autre n'est nécessaire pour que `import _env` fonctionne.

Le dossier créé n'est jamais nettoyé ici : chaque script le vide lui-même à la fin (voir le `assert
S.DATA_DIR == _env.DATA_DIR` qui protège contre un nettoyage accidentel d'un dossier réel) ; le système
d'exploitation finit par purger `tempfile.gettempdir()` de toute façon.
"""
import os
import sys
import tempfile
from pathlib import Path

#: dossier contenant server.py (parent de tests/) — pour que `import server` le trouve, et pour les tests qui
#: lancent `python server.py` en sous-processus (son `cwd`).
SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

DATA_DIR = Path(tempfile.mkdtemp(prefix="waseboard-tests-"))
os.environ["WASEBOARD_DATA_DIR"] = str(DATA_DIR)
