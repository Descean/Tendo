"""Service email – envoi SMTP et surveillance IMAP des réponses."""

import smtplib
import imaplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
from datetime import datetime, timezone

from app.config import settings
from app.utils.logger import logger


async def send_dossier_request(
    authority_email: str,
    publication_reference: str,
    publication_title: str,
    requester_name: str,
    requester_company: Optional[str] = None,
    cc_email: Optional[str] = None,
) -> dict:
    """Envoie un email de demande de dossier d'appel d'offres."""
    subject = f"Demande de dossier - {publication_reference} - {publication_title}"

    company_line = f"\nEntreprise : {requester_company}" if requester_company else ""

    body = f"""Madame, Monsieur,

Par la présente, nous sollicitons l'obtention du dossier d'appel d'offres relatif à :

Référence : {publication_reference}
Objet : {publication_title}

Demandeur : {requester_name}{company_line}

Nous vous prions de bien vouloir nous transmettre le dossier complet de consultation à l'adresse email indiquée ci-dessus, ou de nous indiquer les modalités de retrait.

Dans l'attente de votre retour, nous vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées.

{requester_name}
---
Message envoyé automatiquement via Tendo - Assistant Marchés Publics"""

    msg = MIMEMultipart()
    msg["From"] = settings.smtp_user
    msg["To"] = authority_email
    msg["Subject"] = subject
    if cc_email:
        msg["Cc"] = cc_email

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(settings.smtp_server, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            recipients = [authority_email]
            if cc_email:
                recipients.append(cc_email)
            server.sendmail(settings.smtp_user, recipients, msg.as_string())

        logger.info(f"Email envoyé à {authority_email} pour {publication_reference}")
        return {
            "success": True,
            "to": authority_email,
            "subject": subject,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Erreur envoi email à {authority_email}: {e}")
        return {"success": False, "error": str(e)}


def check_inbox_for_responses(
    subjects_to_match: List[str],
    since_date: Optional[str] = None,
) -> List[dict]:
    """Vérifie la boîte de réception pour des réponses aux demandes de dossiers.

    Args:
        subjects_to_match: Liste de sujets à rechercher (correspondance partielle).
        since_date: Date minimale au format "DD-Mon-YYYY" (ex: "01-Jan-2026").

    Returns:
        Liste de dictionnaires avec les réponses trouvées.
    """
    responses = []

    try:
        mail = imaplib.IMAP4_SSL(settings.imap_server, settings.imap_port)
        mail.login(settings.smtp_user, settings.smtp_password)
        mail.select("INBOX")

        # Recherche par date si fournie
        search_criteria = "ALL"
        if since_date:
            search_criteria = f'(SINCE "{since_date}")'

        _, message_numbers = mail.search(None, search_criteria)

        for num in message_numbers[0].split():
            _, msg_data = mail.fetch(num, "(RFC822)")
            msg = email_lib.message_from_bytes(msg_data[0][1])

            subject = _decode_header(msg["Subject"] or "")
            from_addr = _decode_header(msg["From"] or "")

            # Vérifier si le sujet correspond à une de nos demandes
            for search_subject in subjects_to_match:
                if search_subject.lower() in subject.lower():
                    body = _get_email_body(msg)
                    responses.append({
                        "from": from_addr,
                        "subject": subject,
                        "body": body[:2000],  # Limiter la taille
                        "date": msg["Date"],
                        "matched_subject": search_subject,
                    })
                    break

        mail.logout()
        logger.info(f"Vérification inbox: {len(responses)} réponses trouvées")

    except Exception as e:
        logger.error(f"Erreur vérification inbox: {e}")

    return responses


def _decode_header(header: str) -> str:
    """Décode un en-tête email."""
    decoded_parts = email_lib.header.decode_header(header)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _get_email_body(msg) -> str:
    """Extrait le corps texte d'un email."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""
