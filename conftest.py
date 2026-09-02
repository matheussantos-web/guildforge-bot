"""Configuração de testes do projeto.

Garante que a raiz do repositório seja adicionada ao ``sys.path`` para que o
pacote ``bot`` seja importável independentemente do modo de execução do pytest.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
