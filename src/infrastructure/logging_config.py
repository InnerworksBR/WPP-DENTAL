"""Configuração de logs coloridos para o WPP-DENTAL."""

from __future__ import annotations

import logging
import os

# Cores ANSI — desabilitadas automaticamente se não for terminal (ex: arquivo de log)
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_COLORS = {
    "DEBUG":    "\033[36m",   # ciano
    "INFO":     "\033[32m",   # verde
    "WARNING":  "\033[33m",   # amarelo
    "ERROR":    "\033[31m",   # vermelho
    "CRITICAL": "\033[1;31m", # vermelho negrito
}

# Cores para destacar partes da mensagem
_ENGINE_COLORS = {
    "agent":    "\033[1;35m",  # roxo negrito
    "langgraph": "\033[1;36m", # ciano negrito
    "legacy":   "\033[1;33m",  # amarelo negrito
}

# Loggers ruidosos que não agregam valor no dia a dia
_NOISY_LOGGERS = [
    "httpx",
    "httpcore",
    "openai",
    "langchain",
    "langchain_core",
    "langchain_openai",
    "langgraph",
    "googleapiclient",
    "google",
    "urllib3",
    "asyncio",
]


def _colorize_message(msg: str, use_color: bool) -> str:
    """Destaca tags de engine e prefixos de erro na mensagem."""
    if not use_color:
        return msg
    for engine, color in _ENGINE_COLORS.items():
        tag = f"[ENGINE={engine}]"
        if tag in msg:
            msg = msg.replace(tag, f"{color}{tag}{_RESET}")
    return msg


class ColoredFormatter(logging.Formatter):
    """Formatter com cores ANSI e destaque de engines."""

    def __init__(self, use_color: bool = True) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = _COLORS.get(level, "") if self.use_color else ""
        reset = _RESET if self.use_color else ""
        dim = _DIM if self.use_color else ""
        bold = _BOLD if self.use_color else ""

        # Tempo
        time_str = self.formatTime(record, "%H:%M:%S")

        # Nome do logger — pega só a última parte para economizar espaço
        name = record.name
        short_name = name.split(".")[-1] if "." in name else name

        # Nível formatado com cor e tamanho fixo
        level_str = f"{color}{bold}{level:<8}{reset}"

        # Mensagem com destaque de engines
        message = record.getMessage()
        message = _colorize_message(message, self.use_color)

        # Exceção, se houver
        exc_text = ""
        if record.exc_info:
            exc_text = "\n" + self.formatException(record.exc_info)
            if self.use_color:
                exc_text = f"\033[31m{exc_text}{reset}"

        return (
            f"{dim}{time_str}{reset} "
            f"{level_str} "
            f"{dim}[{short_name}]{reset} "
            f"{message}"
            f"{exc_text}"
        )


def setup_logging(level: str = "INFO") -> None:
    """Configura o logging global da aplicação."""
    use_color = _should_use_color()
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = ColoredFormatter(use_color=use_color)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove handlers existentes para evitar duplicação
    root.handlers.clear()
    root.addHandler(handler)

    # Silencia loggers ruidosos de bibliotecas externas
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Mantém logs de erros do uvicorn/fastapi visíveis
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _should_use_color() -> bool:
    """Detecta se o terminal suporta cores."""
    if os.getenv("NO_COLOR") or os.getenv("WPP_NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR") or os.getenv("WPP_FORCE_COLOR"):
        return True
    import sys
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
