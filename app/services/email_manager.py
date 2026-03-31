"""Service email – envoi SMTP async et surveillance IMAP des reponses.

Utilise aiosmtplib (async) au lieu de smtplib (synchrone/bloquant).
Pour IMAP, on utilise asyncio.to_thread() car aioimaplib est instable.
"""

import asyncio
import imaplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List
from datetime import datetime, timezone

import aiosmtplib

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
    """Envoie un email de demande de dossier d'appel d'offres (async)."""
    subject = f"Demande de dossier - {publication_reference} - {publication_title}"

    company_line = f"\nEntreprise : {requester_company}" if requester_company else ""

    body = f"""Madame, Monsieur,

Par la presente, nous sollicitons l'obtention du dossier d'appel d'offres relatif a :

Reference : {publication_reference}
Objet : {publication_title}

Demandeur : {requester_name}{company_line}

Nous vous prions de bien vouloir nous transmettre le dossier complet de consultation a l'adresse email indiquee ci-dessus, ou de nous indiquer les modalites de retrait.

Dans l'attente de votre retour, nous vous prions d'agreer, Madame, Monsieur, l'expression de nos salutations distinguees.

{requester_name}
---
Message envoye automatiquement via Tendo - Assistant Marches Publics"""

    msg = MIMEMultipart()
    msg["From"] = f"Tendo <{settings.smtp_user}>"
    msg["To"] = authority_email
    msg["Subject"] = subject
    if cc_email:
        msg["Cc"] = cc_email

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        recipients = [authority_email]
        if cc_email:
            recipients.append(cc_email)

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_server,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=True,
        )

        logger.info(f"Email envoye a {authority_email} pour {publication_reference}")
        return {
            "success": True,
            "to": authority_email,
            "subject": subject,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Erreur envoi email a {authority_email}: {e}")
        return {"success": False, "error": str(e)}


async def check_inbox_for_responses(
    subjects_to_match: List[str],
    since_date: Optional[str] = None,
) -> List[dict]:
    """Verifie la boite de reception pour des reponses aux demandes de dossiers.

    Utilise asyncio.to_thread pour ne pas bloquer la boucle asyncio.

    Args:
        subjects_to_match: Liste de sujets a rechercher (correspondance partielle).
        since_date: Date minimale au format "DD-Mon-YYYY" (ex: "01-Jan-2026").

    Returns:
        Liste de dictionnaires avec les reponses trouvees.
    """
    return await asyncio.to_thread(
        _check_inbox_sync, subjects_to_match, since_date
    )


def _check_inbox_sync(
    subjects_to_match: List[str],
    since_date: Optional[str] = None,
) -> List[dict]:
    """Version synchrone de la verification IMAP (executee dans un thread)."""
    responses = []

    if not settings.smtp_user or not settings.smtp_password:
        logger.warning("[Email] SMTP credentials manquants, skip inbox check")
        return responses

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

            # Verifier si le sujet correspond a une de nos demandes
            for search_subject in subjects_to_match:
                if search_subject.lower() in subject.lower():
                    body = _get_email_body(msg)
                    responses.append({
                        "from": from_addr,
                        "subject": subject,
                        "body": body[:2000],
                        "date": msg["Date"],
                        "matched_subject": search_subject,
                    })
                    break

        mail.logout()
        logger.info(f"Verification inbox: {len(responses)} reponses trouvees")

    except Exception as e:
        logger.error(f"Erreur verification inbox: {e}")

    return responses


def _decode_header(header: str) -> str:
    """Decode un en-tete email."""
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
