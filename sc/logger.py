import logging
from logging.handlers import SMTPHandler
import time

from sc import config

# --- CONSTANTE para el nivel mínimo de email ---
# Puedes cambiarlo aquí o en config.SMTP_MIN_LEVEL ("WARNING", "ERROR", etc.)
EMAIL_MIN_LEVEL_NAME = getattr(config, "SMTP_MIN_LEVEL", "WARNING")

# Anti-spam: mínimo N segundos entre emails
_EMAIL_MIN_INTERVAL = 300  # 5 minutos
_last_email_ts = 0.0


def _create_base_handlers():
    """
    Crea los handlers básicos:
      - FileHandler -> sc.log
      - StreamHandler -> consola
    """
    # fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    fmt = "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(funcName)s() - %(message)s"

    formatter = logging.Formatter(fmt)

    file_handler = logging.FileHandler("sc.log", mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    return [file_handler, stream_handler]


class ThrottledSMTPHandler(SMTPHandler):
    """
    SMTPHandler con limitador temporal muy simple para evitar spam:
    solo permite enviar un correo cada _EMAIL_MIN_INTERVAL segundos.
    """
    def emit(self, record: logging.LogRecord) -> None:
        global _last_email_ts
        now = time.time()
        if now - _last_email_ts < _EMAIL_MIN_INTERVAL:
            # Demasiado pronto, ignoramos este mail
            return
        _last_email_ts = now
        super().emit(record)


def _create_smtp_handler():
    """
    Crea un SMTPHandler usando las credenciales de config.py.
    """
    if not getattr(config, "SMTP_ENABLED", False):
        return None

    smtp_from = getattr(config, "SMTP_FROM", None)
    smtp_to = getattr(config, "SMTP_TO", None)
    smtp_user = getattr(config, "SMTP_USER", None)
    smtp_pass = getattr(config, "SMTP_PASSWORD", None)
    smtp_subject = getattr(config, "SMTP_SUBJECT", "⚠ ALERTA sistema RCE")

    if not all([smtp_from, smtp_to, smtp_user, smtp_pass]):
        logging.getLogger(__name__).warning(
            "SMTP habilitado pero faltan campos en config.smtp; "
            "no se configurará el handler SMTP."
        )
        return None

    # Puede ser string "a,b,c" o lista
    if isinstance(smtp_to, str):
        to_addrs = [addr.strip() for addr in smtp_to.split(",") if addr.strip()]
    else:
        to_addrs = list(smtp_to)

    handler = ThrottledSMTPHandler(
        mailhost=("smtp.gmail.com", 587),
        fromaddr=smtp_from,
        toaddrs=to_addrs,
        subject=smtp_subject,
        credentials=(smtp_user, smtp_pass),
        secure=(),  # STARTTLS
    )

    # Nivel mínimo de email, configurable
    level_name = (EMAIL_MIN_LEVEL_NAME or "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    handler.setLevel(level)

    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] %(levelname)s en %(name)s: %(message)s"
        )
    )

    return handler


def get_logger(name: str = "sc") -> logging.Logger:
    """
    Obtiene un logger ya configurado con:
      - nivel config.LOG_LEVEL
      - fichero sc.log
      - salida por consola
      - (opcional) envío por Gmail a partir de EMAIL_MIN_LEVEL_NAME
    """
    logger = logging.getLogger(name)

    # Si ya tiene handlers, no lo reconfiguramos
    if logger.handlers:
        return logger

    # Nivel base desde la config
    base_level_name = getattr(config, "LOG_LEVEL", "INFO").upper()
    base_level = getattr(logging, base_level_name, logging.INFO)
    logger.setLevel(base_level)

    # Handlers básicos
    for h in _create_base_handlers():
        logger.addHandler(h)

    # Handler SMTP
    smtp_handler = _create_smtp_handler()
    if smtp_handler is not None:
        logger.addHandler(smtp_handler)
        logger.debug(
            f"SMTPHandler configurado correctamente. "
            f"Nivel mínimo email = {EMAIL_MIN_LEVEL_NAME}"
        )

    logger.debug("Logger inicializado.")
    return logger