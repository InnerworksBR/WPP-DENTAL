"""Configuracao global de testes.

Pre-carrega o modulo de servicos para resolver a cadeia de importacao circular
que ocorre quando tests/test_agenda_rules.py (ou outros) sao executados em
isolamento, sem que test_admin.py (que importa app.py) seja carregado antes.

A cadeia circular e:
  src.interfaces.tools.__init__
    -> patient_tool -> application.services (via __init__)
       -> clean_agent_service -> interfaces.tools.patient_tool (parcial!)

Quando application.services ja esta em sys.modules (mesmo que parcial), o
patient_tool importa patient_service.py como submodulo diretamente, quebrando
o ciclo. Este conftest garante que a cadeia e resolvida uma vez antes de
qualquer teste.
"""
import os

os.environ.setdefault("DATABASE_PATH", "./data/test_conftest.db")
os.environ.setdefault("OPENAI_API_KEY", "test-key")

# Resolve the circular import by triggering the correct entry point.
# Importing clean_agent_service directly (not via __init__) starts the
# resolution from the application layer, where application.services is
# already partial in sys.modules when patient_tool tries to import it.
try:
    from src.application.services.clean_agent_service import CleanAgentService  # noqa: F401
except Exception:
    pass  # Best-effort: if env is incomplete, individual tests handle their setup.
