"""
utils_email.py
--------------
Utilidad para enviar correos de alerta usando SMTP de Gmail.
"""

import smtplib
import ssl
from email.message import EmailMessage
import logging
import os


# ---------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------
# Se recomienda usar variables de entorno en vez de escribir aquí claves.
GMAIL_USER = os.getenv("GMAIL_USER", "drdasilvaverbel@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "Capivara03!")


def enviar_email_error(asunto: str, cuerpo: str, destinatario: str) -> bool:
    """
    Envía un correo de alerta usando el SMTP de Gmail.
    Devuelve True si se envió correctamente, False si falló.

    Requisitos:
        - Activar 2FA en Gmail
        - Crear contraseña de aplicación en Google
    """
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = destinatario
    msg["Subject"] = asunto
    msg.set_content(cuerpo)

    context = ssl.create_default_context()

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls(context=context)
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.send_message(msg)

        logging.info(f"[EMAIL] Alerta enviada a {destinatario}.")
        return True

    except Exception as e:
        logging.error(f"[EMAIL] Error enviando correo: {e}")
        return False