import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Credenciales proporcionadas
EMAIL_USER = "rce.ai.udl@gmail.com"
EMAIL_PASS = "qwcj ttpt oqzd nsds"  # Contraseña de aplicación generada en Gmail

# Destinatario (puede ser el mismo para pruebas)
EMAIL_TO = "vitor.dasilva@udl.cat,joseplluis.lerida@udl.cat"

def enviar_email_test():
    # Crear el mensaje
    msg = MIMEMultipart()
    msg["From"] = EMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = "Sistema RCE"

    cuerpo = "Este es un email enviado desde el sistema, confirmame la correcta recepción por whatsapp ;) ."
    msg.attach(MIMEText(cuerpo, "plain"))

    try:
        # Conexión SMTP segura usando STARTTLS (puerto 587)
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()

        # Login con app password
        server.login(EMAIL_USER, EMAIL_PASS)

        # Enviar email
        server.send_message(msg)

        print("✔ Email enviado correctamente.")
    except Exception as e:
        print("❌ Error enviando email:", str(e))
    finally:
        server.quit()


if __name__ == "__main__":
    enviar_email_test()