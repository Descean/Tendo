"""Router webhook WhatsApp -- supporte Meta Cloud API ET Twilio."""

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Request, Response, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.utils.db import get_db
from app.utils.logger import logger
from app.models.user import User, SubscriptionStatus
from app.models.publication import Publication
from app.models.notification import Notification
from app.models.email_tracking import EmailTracking
from app.services import whatsapp, claude
from app.services.whatsapp import (
    WELCOME_MESSAGE, MENU_MESSAGE, PLANS_MESSAGE, SUBSCRIPTION_EXPIRED,
    meta_verify_signature, _meta_verify_webhook,
)
from app.services.payment import create_payment_link
from app.services.email_manager import send_dossier_request
from app.services.document_analyzer import (
    extract_pdf_text, extract_image_text, download_whatsapp_media,
    analyze_document_full, check_relevance_for_user,
)
from app.services.document_classifier import classify_document, classify_with_ai

router = APIRouter(prefix="/webhook", tags=["Webhook"])

PROVIDER = settings.whatsapp_provider

# Nombre max de messages dans l'historique de conversation
MAX_HISTORY = 8


# ================================================
#  HISTORIQUE DE CONVERSATION
# ================================================

def _get_conversation_history(user: User) -> List[dict]:
    """Recupere l'historique de conversation depuis conversation_data."""
    data = user.conversation_data or {}
    return data.get("history", [])


def _save_conversation_history(user: User, user_msg: str, bot_msg: str):
    """Sauvegarde un echange dans l'historique de conversation.

    Garde uniquement les N derniers messages pour controler les tokens.
    """
    data = dict(user.conversation_data or {})
    history = data.get("history", [])

    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": bot_msg[:500]})  # Tronquer pour economiser

    # Garder seulement les derniers messages
    if len(history) > MAX_HISTORY * 2:
        history = history[-(MAX_HISTORY * 2):]

    data["history"] = history
    user.conversation_data = data
    flag_modified(user, "conversation_data")


# ================================================
#  WEBHOOK META WHATSAPP CLOUD API
# ================================================

@router.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """Verification du webhook (Meta envoie un GET avec challenge)."""
    if PROVIDER == "meta":
        params = dict(request.query_params)
        challenge = _meta_verify_webhook(params)
        if challenge:
            return PlainTextResponse(content=challenge)
        raise HTTPException(status_code=403, detail="Verification failed")
    return {"status": "ok", "service": "Tendo WhatsApp Webhook"}


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Webhook pour les messages WhatsApp entrants (Meta ou Twilio)."""
    if PROVIDER == "meta":
        return await _handle_meta_webhook(request, db)
    else:
        return await _handle_twilio_webhook(request, db)


async def _handle_meta_webhook(request: Request, db: AsyncSession):
    """Traite un webhook Meta WhatsApp Cloud API."""
    raw_body = await request.body()

    if settings.meta_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not meta_verify_signature(raw_body, signature):
            logger.warning("[Meta] Signature webhook invalide")
            raise HTTPException(status_code=403, detail="Invalid signature")

    body = json.loads(raw_body)

    if body.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            for msg in messages:
                msg_type = msg.get("type")
                from_number = msg.get("from", "")

                if msg_type == "text":
                    text = msg["text"]["body"].strip()
                    await _process_message(f"+{from_number}", text, db)
                elif msg_type == "interactive":
                    # Reponses aux boutons et listes interactifs
                    interactive = msg.get("interactive", {})
                    if interactive.get("type") == "button_reply":
                        text = interactive["button_reply"]["id"]
                        await _process_message(f"+{from_number}", text, db)
                    elif interactive.get("type") == "list_reply":
                        text = interactive["list_reply"]["id"]
                        await _process_message(f"+{from_number}", text, db)
                elif msg_type == "document":
                    # Document envoye (PDF, Word, etc.)
                    doc_info = msg.get("document", {})
                    media_id = doc_info.get("id", "")
                    filename = doc_info.get("filename", "document")
                    mime = doc_info.get("mime_type", "")
                    caption = doc_info.get("caption", "")
                    await _process_document(
                        f"+{from_number}", media_id, filename, mime, caption, db
                    )
                elif msg_type == "image":
                    # Image envoyee (photo d'un document)
                    img_info = msg.get("image", {})
                    media_id = img_info.get("id", "")
                    mime = img_info.get("mime_type", "image/jpeg")
                    caption = img_info.get("caption", "")
                    await _process_image(
                        f"+{from_number}", media_id, mime, caption, db
                    )

    return {"status": "ok"}


async def _handle_twilio_webhook(request: Request, db: AsyncSession):
    """Traite un webhook Twilio."""
    form_data = await request.form()
    from_number = form_data.get("From", "").replace("whatsapp:", "")
    body = form_data.get("Body", "").strip()

    if not from_number or not body:
        return Response(content="<Response></Response>", media_type="application/xml")

    await _process_message(from_number, body, db)
    return Response(content="<Response></Response>", media_type="application/xml")


# ================================================
#  LOGIQUE METIER (commune aux 2 providers)
# ================================================

async def _process_message(from_number: str, body: str, db: AsyncSession):
    """Traite un message WhatsApp entrant."""
    logger.info(f"Message WhatsApp de {from_number}: {body[:100]}")

    result = await db.execute(select(User).where(User.phone_number == from_number))
    user = result.scalar_one_or_none()

    if not user:
        # Anti-fraude : vérifier si ce numéro est un ancien utilisateur qui revient
        # (changement de numéro impossible sur WhatsApp, mais vérifions quand même)
        existing_expired = await db.execute(
            select(User).where(
                User.phone_number == from_number,
                User.has_used_trial == True,
            )
        )
        was_trial = existing_expired.scalar_one_or_none()

        user = User(
            phone_number=from_number,
            subscription_status=SubscriptionStatus.TRIAL.value,
            trial_end=datetime.now(timezone.utc) + timedelta(days=7),
            has_used_trial=True,
        )
        db.add(user)
        await db.flush()
        # Accueil interactif avec boutons
        await whatsapp.send_interactive_buttons(
            from_number,
            "Bienvenue sur Tendo !\n\n"
            "Votre assistant de veille sur les marches publics au Benin.\n\n"
            "Vous beneficiez d'un essai gratuit de 7 jours.\n"
            "Inscrivez-vous pour recevoir des alertes personnalisees.",
            [
                {"id": "menu_inscription", "title": "S'inscrire"},
                {"id": "menu_menu", "title": "Voir le menu"},
            ],
            header="Bienvenue !",
            footer="tendo.shiftup.bj",
        )
        return

    # Verifier le trial expire
    if (
        user.subscription_status == SubscriptionStatus.TRIAL.value
        and user.trial_end
        and user.trial_end < datetime.now(timezone.utc)
    ):
        user.subscription_status = SubscriptionStatus.EXPIRED.value
        logger.info(f"[Trial] Expire pour {from_number}")

    # Verifier l'abonnement expire
    if user.subscription_status == SubscriptionStatus.EXPIRED.value:
        allowed_intents = ("ABONNEMENT", "PAIEMENT", "SUPPORT")
        intent_result = await claude.detect_intent(body)
        if intent_result["intent"] not in allowed_intents:
            await whatsapp.send_interactive_buttons(
                from_number,
                "Votre periode d'essai a expire.\n\n"
                "Pour continuer a recevoir vos alertes marches publics, "
                "souscrivez a un abonnement.",
                [
                    {"id": "menu_abonnement", "title": "Voir les plans"},
                    {"id": "menu_support", "title": "Contacter support"},
                ],
                header="Abonnement expire",
                footer="tendo.shiftup.bj",
            )
            return
        await _handle_intent(intent_result["intent"], body, user, from_number, db)
        return

    # Gerer le flux d'inscription en cours
    if user.conversation_state and user.conversation_state.startswith("inscription_"):
        reply = await _handle_registration_flow(user, body, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Gerer le flux de modification de profil
    if user.conversation_state and user.conversation_state.startswith("modif_"):
        reply = await _handle_profile_modification_flow(user, body, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Gerer la confirmation de suppression
    if user.conversation_state == "confirm_delete":
        reply = await _handle_delete_confirmation(user, body, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Reponses aux boutons du menu interactif
    if body.startswith("menu_"):
        menu_action = body.replace("menu_", "")
        intent_map = {
            "menu": "MENU", "inscription": "INSCRIPTION", "historique": "HISTORIQUE",
            "abonnement": "ABONNEMENT", "paiement": "PAIEMENT", "profil": "MODIFIER_PROFIL",
            "support": "SUPPORT", "pay_essentiel": "PAIEMENT", "pay_premium": "PAIEMENT",
        }
        intent = intent_map.get(menu_action)
        if intent:
            # Pour paiement, passer le plan dans body
            if menu_action == "pay_premium":
                body = "premium"
            elif menu_action == "pay_essentiel":
                body = "essentiel"
            await _handle_intent(intent, body, user, from_number, db)
            return

    # Reponses aux boutons interactifs (notifications)
    if body.startswith("voir_"):
        await _handle_button_voir(body, from_number, db)
        return
    if body.startswith("savoir_"):
        await _handle_button_savoir(body, from_number, user, db)
        return
    if body.startswith("demander_"):
        await _handle_button_demander(body, from_number, user, db)
        return

    # Commandes speciales
    msg_lower = body.lower().strip()
    if "/demander_dossier" in msg_lower or "demander le dossier" in msg_lower:
        reply = await _handle_dossier_request(body, user, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Analyse de document (/analyser REF ou "analyse AO-...")
    if "/analyser" in msg_lower or (
        any(w in msg_lower for w in ("analyse", "analyser", "details de", "detail de"))
        and "ao-" in msg_lower
    ):
        reply = await _handle_document_analysis(body, user, db)
        await whatsapp.send_message(from_number, reply)
        return

    # Detection d'intention
    intent_result = await claude.detect_intent(body)
    intent = intent_result["intent"]

    if intent == "QUESTION":
        # Conversation IA libre avec historique
        is_premium = user.subscription_plan == "premium"
        history = _get_conversation_history(user)
        reply = await claude.chat(body, is_premium=is_premium, conversation_history=history, db_session=db)
        _save_conversation_history(user, body, reply)
        await whatsapp.send_message(from_number, reply)
    else:
        await _handle_intent(intent, body, user, from_number, db)


async def _handle_intent(intent: str, body: str, user: User, phone: str, db: AsyncSession):
    """Gere l'intention detectee et envoie la reponse interactive."""

    if intent == "MENU":
        await _send_menu(phone, user)
        return

    elif intent == "INSCRIPTION":
        if user.name and user.sectors:
            await whatsapp.send_interactive_buttons(
                phone,
                f"Vous etes deja inscrit.\n\n"
                f"Nom : {user.name}\n"
                f"Secteurs : {', '.join(user.sectors) if user.sectors else 'Tous'}\n"
                f"Regions : {', '.join(user.regions) if user.regions else 'Tout le Benin'}",
                [
                    {"id": "menu_profil", "title": "Modifier profil"},
                    {"id": "menu_historique", "title": "Mes alertes"},
                ],
                header="Profil Tendo",
                footer="tendo.shiftup.bj",
            )
            return
        user.conversation_state = "inscription_nom"
        user.conversation_data = {}
        flag_modified(user, "conversation_data")
        await whatsapp.send_message(phone,
            "INSCRIPTION TENDO\n\n"
            "Commencons votre inscription.\n"
            "Veuillez saisir votre nom complet :"
        )
        return

    elif intent == "MODIFIER_PROFIL":
        reply = _start_profile_modification(user)
        await whatsapp.send_message(phone, reply)
        return

    elif intent == "SUPPRIMER_COMPTE":
        reply = _start_account_deletion(user)
        await whatsapp.send_message(phone, reply)
        return

    elif intent == "ABONNEMENT":
        await _send_plans(phone)
        return

    elif intent == "HISTORIQUE":
        await _send_history_interactive(user, phone, db)
        return

    elif intent == "PAIEMENT":
        body_lower = body.lower()
        plan = "premium" if "premium" in body_lower else "essentiel"
        reply = await _handle_payment(user, plan=plan)
        await whatsapp.send_message(phone, reply)
        return

    elif intent == "SUPPORT":
        await whatsapp.send_interactive_buttons(
            phone,
            "Un agent va vous contacter prochainement.\n"
            "En attendant, vous pouvez nous ecrire a : support@shiftup.bj",
            [
                {"id": "menu_menu", "title": "Retour au menu"},
            ],
            header="Support Tendo",
            footer="tendo.shiftup.bj",
        )
        return

    elif intent == "DEMANDE_DOSSIER":
        reply = await _handle_dossier_request(body, user, db)
        await whatsapp.send_message(phone, reply)
        return

    else:
        is_premium = user.subscription_plan == "premium"
        history = _get_conversation_history(user)
        reply = await claude.chat(body, is_premium=is_premium, conversation_history=history, db_session=db)
        _save_conversation_history(user, body, reply)
        await whatsapp.send_message(phone, reply)


async def _send_menu(phone: str, user: User):
    """Envoie le menu principal avec liste interactive."""
    status = user.subscription_status or "trial"
    status_label = {"trial": "Essai gratuit", "active": "Actif", "expired": "Expire"}.get(status, status)
    plan_label = f" ({user.subscription_plan})" if user.subscription_plan else ""

    await whatsapp.send_interactive_list(
        phone,
        f"Bienvenue sur Tendo !\n\n"
        f"Votre formule : *{status_label.capitalize()}{plan_label}*\n\n"
        f"Choisissez une option ci-dessous ou posez directement votre question.\n"
        f"tendo.shiftup.bj",
        "Voir le menu",
        [{
            "title": "Services Tendo",
            "rows": [
                {"id": "menu_inscription", "title": "Inscription", "description": "Configurer vos preferences secteurs/regions"},
                {"id": "menu_historique", "title": "Mes dernieres alertes", "description": "Voir les appels d'offres recents"},
                {"id": "menu_abonnement", "title": "Abonnement", "description": "Voir les plans et tarifs"},
                {"id": "menu_paiement", "title": "Paiement", "description": "Gerer votre abonnement"},
                {"id": "menu_profil", "title": "Modifier profil", "description": "Changer secteurs, regions, sources"},
                {"id": "menu_support", "title": "Support", "description": "Contacter un agent"},
            ],
        }],
        footer="Tendo - Marches Publics Benin",
    )


async def _send_plans(phone: str):
    """Envoie les plans d'abonnement avec boutons interactifs."""
    await whatsapp.send_interactive_buttons(
        phone,
        "PLANS D'ABONNEMENT TENDO\n\n"
        "--- Essentiel -- 5 000 FCFA/mois ---\n"
        "Alertes personnalisees, resumes IA, recherche\n\n"
        "--- Premium -- 15 000 FCFA/mois ---\n"
        "Tout Essentiel + IA expert, dossiers AO, email monitoring, support prioritaire",
        [
            {"id": "menu_pay_essentiel", "title": "Essentiel 5000F"},
            {"id": "menu_pay_premium", "title": "Premium 15000F"},
            {"id": "menu_menu", "title": "Retour au menu"},
        ],
        header="Abonnement",
        footer="tendo.shiftup.bj",
    )


async def _send_history_interactive(user: User, phone: str, db: AsyncSession):
    """Envoie l'historique avec liste interactive des publications."""
    history_text = await _get_history(user, db)

    # Recuperer les publications recentes pour les boutons
    from sqlalchemy import and_
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = await db.execute(
        select(Publication).where(
            Publication.created_at >= cutoff
        ).order_by(Publication.created_at.desc()).limit(10)
    )
    pubs = result.scalars().all()

    if pubs:
        rows = []
        for pub in pubs[:10]:
            title = pub.title[:24] if pub.title else f"Pub #{pub.id}"
            desc = f"{pub.source} - {pub.document_type or 'AO'}"[:72]
            rows.append({
                "id": f"voir_{pub.id}",
                "title": title,
                "description": desc,
            })

        await whatsapp.send_interactive_list(
            phone,
            f"Voici les {len(pubs)} dossiers actifs.\n\n"
            f"Selectionnez un dossier pour voir les details complets.",
            "Voir les dossiers",
            [{"title": "Publications recentes", "rows": rows}],
            footer="Tendo - Marches Publics Benin",
        )
    else:
        await whatsapp.send_interactive_buttons(
            phone,
            history_text,
            [{"id": "menu_menu", "title": "Retour au menu"}],
            footer="tendo.shiftup.bj",
        )


# ================================================
#  INSCRIPTION AVEC VALIDATION
# ================================================

SECTEURS_MAP = {
    "1": "BTP", "2": "Fournitures", "3": "Services", "4": "TIC",
    "5": "Sante", "6": "Education", "7": "Agriculture",
    "8": "Environnement", "9": "Transport", "10": "Energie",
}

REGIONS_MAP = {
    "1": "Cotonou", "2": "Porto-Novo", "3": "Parakou", "4": "Abomey",
    "5": "Bohicon", "6": "Djougou", "7": "Natitingou", "8": "Lokossa",
    "9": "Tout le Benin", "10": "CEDEAO",
}

SOURCES_MAP = {
    "1": "marches-publics.bj", "2": "ARMP", "3": "gouv.bj",
    "4": "ADPME", "5": "ABE", "6": "BAD", "7": "AFD", "8": "Toutes",
}

INVALID_NAMES = {
    "dude", "test", "toto", "xxx", "aaa", "bbb", "lol", "ok", "oui",
    "non", "salut", "bonjour", "hey", "yo", "sup", "coucou",
}


def _validate_name(text: str) -> bool:
    """Verifie que le texte ressemble a un nom."""
    cleaned = text.strip()
    if len(cleaned) < 2:
        return False
    letter_count = sum(1 for c in cleaned if c.isalpha())
    if letter_count < 2:
        return False
    if cleaned.lower() in INVALID_NAMES:
        return False
    return True


def _validate_company_name(text: str) -> bool:
    """Verifie que le texte ressemble a un nom d'entreprise valide."""
    cleaned = text.strip()
    if len(cleaned) < 2:
        return False
    if cleaned.lower() in INVALID_NAMES:
        return False
    if len(cleaned.split()) == 1 and len(cleaned) < 3:
        return False
    return True


def _parse_numeric_choices(text: str, valid_map: dict) -> list:
    """Parse les choix numeriques separes par virgules."""
    parts = [p.strip() for p in text.replace(" ", ",").replace(";", ",").split(",")]
    selected = []
    for p in parts:
        if p in valid_map:
            selected.append(valid_map[p])
    return selected


async def _ai_parse_choices(text: str, category: str, valid_map: dict) -> list:
    """Utilise l'IA pour interpreter les reponses en langage naturel."""
    numeric = _parse_numeric_choices(text, valid_map)
    if numeric:
        return numeric

    options_text = "\n".join(f"{k} = {v}" for k, v in valid_map.items())
    prompt = (
        f"L'utilisateur repond a la question '{category}' avec : \"{text}\"\n\n"
        f"Voici les options disponibles :\n{options_text}\n\n"
        f"Identifie les numeros correspondants a sa reponse. "
        f"Reponds UNIQUEMENT avec les numeros separes par des virgules, sans texte.\n"
        f"Si aucune option ne correspond, reponds : AUCUN\n"
        f"Exemples : '1,3,4' ou '2' ou 'AUCUN'"
    )
    system = "Tu es un assistant qui extrait des choix numeriques a partir de texte libre. Reponds uniquement avec les numeros ou AUCUN."

    try:
        result = await claude.chat(prompt, is_premium=False)
        if result and result.strip().upper() != "AUCUN":
            return _parse_numeric_choices(result.strip(), valid_map)
    except Exception:
        pass

    return []


async def _ai_validate_input(text: str, field: str, context: str) -> dict:
    """Utilise l'IA pour valider et extraire une valeur d'un champ."""
    prompt = (
        f"L'utilisateur repond a la question '{field}' avec : \"{text}\"\n\n"
        f"Contexte : {context}\n\n"
        f"Analyse cette reponse :\n"
        f"1. Est-ce une reponse valide pour ce champ ?\n"
        f"2. Si oui, extrais la valeur nettoyee\n"
        f"3. Si non, explique pourquoi brievement\n\n"
        f"Reponds dans ce format EXACT (une seule ligne) :\n"
        f"VALIDE: [valeur extraite]\n"
        f"ou\n"
        f"INVALIDE: [raison courte]"
    )
    system = "Tu valides des entrees utilisateur. Reponds uniquement VALIDE: ou INVALIDE: suivi du contenu."

    try:
        result = await claude.chat(prompt, is_premium=False)
        result = result.strip()
        if result.upper().startswith("VALIDE:"):
            value = result[7:].strip().strip('"').strip("'")
            return {"valid": True, "value": value}
        elif result.upper().startswith("INVALIDE:"):
            reason = result[9:].strip()
            return {"valid": False, "reason": reason}
    except Exception:
        pass

    return {"valid": True, "value": text.strip()}


async def _handle_registration_flow(user: User, body: str, db: AsyncSession) -> str:
    """Gere le flux d'inscription pas a pas avec validation IA."""
    state = user.conversation_state
    data = dict(user.conversation_data or {})
    # Preserver l'historique existant
    history = data.get("history", [])
    text = body.strip()

    if text.lower() in ("annuler", "cancel", "stop"):
        user.conversation_state = None
        data_clean = {"history": history}
        user.conversation_data = data_clean
        flag_modified(user, "conversation_data")
        return "Inscription annulee.\nTapez *Menu* pour revenir au menu principal."

    if state == "inscription_nom":
        validation = await _ai_validate_input(
            text,
            "nom complet (prenom et nom)",
            "L'utilisateur s'inscrit sur Tendo. On lui demande son prenom et nom de famille. "
            "Un mot unique n'est pas un nom complet. Des mots comme 'dude', 'test', 'ok' ne sont pas des noms."
        )

        if not validation["valid"]:
            return (
                f"Ce nom ne semble pas valide : {validation.get('reason', '')}\n\n"
                "Veuillez entrer votre prenom et nom de famille.\n"
                "Exemple : Jean Dupont\n\n"
                "Tapez *Annuler* pour quitter l'inscription."
            )

        name = validation["value"].title()
        if not _validate_name(name):
            return (
                "Veuillez entrer votre prenom et nom (minimum 2 caracteres).\n"
                "Exemple : Jean Dupont\n\n"
                "Tapez *Annuler* pour quitter l'inscription."
            )

        data["name"] = name
        data["history"] = history
        user.conversation_data = data
        flag_modified(user, "conversation_data")
        user.conversation_state = "inscription_entreprise"
        return (
            f"Nom enregistre : {data['name']}\n\n"
            "Quel est le nom de votre entreprise ?\n"
            "Tapez *Passer* si vous etes un particulier ou freelancer."
        )

    elif state == "inscription_entreprise":
        skip_words = ("passer", "pass", "non", "-", "aucune", "aucun",
                      "pas d'entreprise", "independant")
        msg_lower = text.lower().strip()

        is_skip = msg_lower in skip_words or any(
            w in msg_lower for w in ("freelance", "particulier", "independant",
                                     "pas d'entreprise", "je n'ai pas")
        )

        if not is_skip:
            validation = await _ai_validate_input(
                text,
                "nom d'entreprise",
                "L'utilisateur donne le nom de son entreprise. "
                "Accepter les acronymes (BTP SARL, COGEB), les noms complets, les SARL/SA/SUARL. "
                "Rejeter les mots seuls absurdes (dude, test, lol, ok). "
                "Si l'utilisateur dit qu'il est freelance/independant/particulier, repondre INVALIDE: PASSER."
            )

            if not validation["valid"]:
                if "PASSER" in validation.get("reason", "").upper():
                    is_skip = True
                else:
                    return (
                        f"Ce nom d'entreprise ne semble pas valide.\n"
                        f"{validation.get('reason', '')}\n\n"
                        "Entrez le nom de votre entreprise ou tapez *Passer*."
                    )

            if not is_skip:
                company = validation.get("value", text.strip())
                if not _validate_company_name(company):
                    return "Veuillez entrer un nom d'entreprise valide ou tapez *Passer*."
                data["company"] = company

        data["history"] = history
        user.conversation_data = data
        flag_modified(user, "conversation_data")
        user.conversation_state = "inscription_secteurs"
        return (
            "SECTEURS D'INTERET\n\n"
            "Choisissez vos secteurs (numeros ou texte libre) :\n\n"
            "1 - BTP\n"
            "2 - Fournitures\n"
            "3 - Services\n"
            "4 - TIC (Informatique)\n"
            "5 - Sante\n"
            "6 - Education\n"
            "7 - Agriculture\n"
            "8 - Environnement\n"
            "9 - Transport\n"
            "10 - Energie\n\n"
            "Vous pouvez envoyer les numeros (ex: 1,3,4) ou ecrire directement vos secteurs."
        )

    elif state == "inscription_secteurs":
        selected = await _ai_parse_choices(text, "secteurs d'interet", SECTEURS_MAP)
        if not selected:
            return (
                "Je n'ai pas pu identifier vos secteurs.\n\n"
                "Envoyez les numeros (ex: 1,3,5) ou ecrivez directement.\n"
                "Exemples : \"BTP et informatique\" ou \"1,4\"\n\n"
                "Secteurs : BTP, Fournitures, Services, TIC, Sante, Education, "
                "Agriculture, Environnement, Transport, Energie"
            )
        data["sectors"] = selected
        data["history"] = history
        user.conversation_data = data
        flag_modified(user, "conversation_data")
        user.conversation_state = "inscription_regions"
        return (
            "Secteurs enregistres : " + ", ".join(selected) + "\n\n"
            "REGIONS D'INTERET\n\n"
            "1 - Cotonou\n"
            "2 - Porto-Novo\n"
            "3 - Parakou\n"
            "4 - Abomey\n"
            "5 - Bohicon\n"
            "6 - Djougou\n"
            "7 - Natitingou\n"
            "8 - Lokossa\n"
            "9 - Tout le Benin\n"
            "10 - CEDEAO\n\n"
            "Numeros ou texte libre (ex: \"Cotonou et Parakou\" ou \"9\" pour tout le Benin)."
        )

    elif state == "inscription_regions":
        selected = await _ai_parse_choices(text, "regions d'interet au Benin", REGIONS_MAP)
        if not selected:
            return (
                "Je n'ai pas pu identifier vos regions.\n\n"
                "Exemples : \"Cotonou et Porto-Novo\" ou \"1,2\" ou \"9\" pour tout le Benin."
            )
        data["regions"] = selected
        data["history"] = history
        user.conversation_data = data
        flag_modified(user, "conversation_data")
        user.conversation_state = "inscription_sources"
        return (
            "Regions enregistrees : " + ", ".join(selected) + "\n\n"
            "SOURCES A SURVEILLER\n\n"
            "1 - marches-publics.bj\n"
            "2 - ARMP\n"
            "3 - gouv.bj\n"
            "4 - ADPME\n"
            "5 - ABE\n"
            "6 - BAD (Banque Africaine de Developpement)\n"
            "7 - AFD (Agence Francaise de Developpement)\n"
            "8 - Toutes les sources\n\n"
            "Numeros ou texte libre. Tapez 8 ou \"toutes\" pour tout surveiller."
        )

    elif state == "inscription_sources":
        if text.strip().lower() in ("toutes", "tout", "toutes les sources", "all", "8"):
            selected = [v for k, v in SOURCES_MAP.items() if k != "8"]
        else:
            selected = await _ai_parse_choices(text, "sources de marches publics", SOURCES_MAP)

        if not selected:
            return (
                "Je n'ai pas pu identifier vos sources.\n\n"
                "Exemples : \"ARMP et gouv.bj\" ou \"1,2,3\" ou \"toutes\"."
            )
        if "Toutes" in selected:
            selected = [v for k, v in SOURCES_MAP.items() if k != "8"]
        data["sources"] = selected

        # Finaliser l'inscription
        user.name = data.get("name", user.name)
        user.company = data.get("company")
        user.sectors = data.get("sectors", [])
        user.regions = data.get("regions", [])
        user.preferred_sources = data.get("sources", [])
        user.conversation_state = None
        user.conversation_data = {"history": history}
        flag_modified(user, "conversation_data")

        company_line = f"Entreprise : {user.company}" if user.company else "Entreprise : Particulier"

        return (
            "INSCRIPTION TERMINEE\n\n"
            f"Nom : {user.name}\n"
            f"{company_line}\n"
            f"Secteurs : {', '.join(user.sectors)}\n"
            f"Regions : {', '.join(user.regions)}\n"
            f"Sources : {', '.join(user.preferred_sources)}\n\n"
            "Vous recevrez vos premieres alertes des demain.\n\n"
            "Tapez *Menu* pour voir les options."
        )

    user.conversation_state = None
    return MENU_MESSAGE


# ================================================
#  MODIFICATION DE PROFIL
# ================================================

def _start_profile_modification(user: User) -> str:
    """Demarre le flux de modification de profil."""
    if not user.name:
        return "Vous n'etes pas encore inscrit. Tapez *Inscription* pour commencer."

    user.conversation_state = "modif_choix"
    current = (
        "VOTRE PROFIL ACTUEL\n\n"
        f"1 - Nom : {user.name}\n"
        f"2 - Entreprise : {user.company or 'Particulier'}\n"
        f"3 - Secteurs : {', '.join(user.sectors) if user.sectors else 'Aucun'}\n"
        f"4 - Regions : {', '.join(user.regions) if user.regions else 'Aucune'}\n"
        f"5 - Sources : {', '.join(user.preferred_sources) if user.preferred_sources else 'Aucune'}\n\n"
        "Quel element souhaitez-vous modifier ?\n"
        "Envoyez le numero (1 a 5) ou tapez *Annuler*."
    )
    return current


async def _handle_profile_modification_flow(user: User, body: str, db: AsyncSession) -> str:
    """Gere la modification de profil etape par etape."""
    state = user.conversation_state
    text = body.strip()

    if text.lower() in ("annuler", "cancel", "stop", "menu"):
        user.conversation_state = None
        return "Modification annulee.\nTapez *Menu* pour revenir au menu."

    if state == "modif_choix":
        if text == "1":
            user.conversation_state = "modif_nom"
            return f"Nom actuel : {user.name}\n\nEntrez votre nouveau nom complet :"
        elif text == "2":
            user.conversation_state = "modif_entreprise"
            return (
                f"Entreprise actuelle : {user.company or 'Particulier'}\n\n"
                "Entrez le nouveau nom ou tapez *Passer* pour rester particulier."
            )
        elif text == "3":
            user.conversation_state = "modif_secteurs"
            return (
                f"Secteurs actuels : {', '.join(user.sectors) if user.sectors else 'Aucun'}\n\n"
                "SECTEURS DISPONIBLES\n\n"
                "1 - BTP\n2 - Fournitures\n3 - Services\n4 - TIC\n"
                "5 - Sante\n6 - Education\n7 - Agriculture\n"
                "8 - Environnement\n9 - Transport\n10 - Energie\n\n"
                "Envoyez les numeros de vos nouveaux secteurs (ex: 1,3,4)."
            )
        elif text == "4":
            user.conversation_state = "modif_regions"
            return (
                f"Regions actuelles : {', '.join(user.regions) if user.regions else 'Aucune'}\n\n"
                "REGIONS DISPONIBLES\n\n"
                "1 - Cotonou\n2 - Porto-Novo\n3 - Parakou\n4 - Abomey\n"
                "5 - Bohicon\n6 - Djougou\n7 - Natitingou\n8 - Lokossa\n"
                "9 - Tout le Benin\n10 - CEDEAO\n\n"
                "Envoyez les numeros (ex: 1,2 ou 9 pour tout)."
            )
        elif text == "5":
            user.conversation_state = "modif_sources"
            return (
                f"Sources actuelles : {', '.join(user.preferred_sources) if user.preferred_sources else 'Aucune'}\n\n"
                "SOURCES DISPONIBLES\n\n"
                "1 - marches-publics.bj\n2 - ARMP\n3 - gouv.bj\n"
                "4 - ADPME\n5 - ABE\n6 - BAD\n7 - AFD\n8 - Toutes\n\n"
                "Envoyez les numeros ou tapez \"toutes\"."
            )
        else:
            return "Choix invalide. Envoyez un numero de 1 a 5 ou tapez *Annuler*."

    elif state == "modif_nom":
        validation = await _ai_validate_input(
            text, "nom complet",
            "Validation d'un nom de personne. Rejeter les noms absurdes."
        )
        if not validation["valid"] or not _validate_name(validation.get("value", text)):
            return "Ce nom n'est pas valide. Entrez votre prenom et nom ou tapez *Annuler*."
        user.name = validation["value"].title()
        user.conversation_state = None
        return f"Nom mis a jour : {user.name}\n\nTapez *Menu* pour continuer."

    elif state == "modif_entreprise":
        if text.lower() in ("passer", "particulier", "aucune"):
            user.company = None
            user.conversation_state = None
            return "Entreprise supprimee (particulier).\n\nTapez *Menu* pour continuer."
        validation = await _ai_validate_input(
            text, "nom d'entreprise",
            "Validation d'un nom d'entreprise. Rejeter les noms absurdes."
        )
        if not validation["valid"]:
            return "Ce nom d'entreprise n'est pas valide. Reessayez ou tapez *Annuler*."
        user.company = validation.get("value", text.strip())
        user.conversation_state = None
        return f"Entreprise mise a jour : {user.company}\n\nTapez *Menu* pour continuer."

    elif state == "modif_secteurs":
        selected = await _ai_parse_choices(text, "secteurs d'interet", SECTEURS_MAP)
        if not selected:
            return "Je n'ai pas identifie vos secteurs. Envoyez les numeros (ex: 1,3,5) ou tapez *Annuler*."
        user.sectors = selected
        user.conversation_state = None
        return f"Secteurs mis a jour : {', '.join(selected)}\n\nTapez *Menu* pour continuer."

    elif state == "modif_regions":
        selected = await _ai_parse_choices(text, "regions d'interet", REGIONS_MAP)
        if not selected:
            return "Je n'ai pas identifie vos regions. Envoyez les numeros ou tapez *Annuler*."
        user.regions = selected
        user.conversation_state = None
        return f"Regions mises a jour : {', '.join(selected)}\n\nTapez *Menu* pour continuer."

    elif state == "modif_sources":
        if text.strip().lower() in ("toutes", "tout", "all", "8"):
            selected = [v for k, v in SOURCES_MAP.items() if k != "8"]
        else:
            selected = await _ai_parse_choices(text, "sources", SOURCES_MAP)
        if not selected:
            return "Je n'ai pas identifie vos sources. Envoyez les numeros ou tapez *Annuler*."
        if "Toutes" in selected:
            selected = [v for k, v in SOURCES_MAP.items() if k != "8"]
        user.preferred_sources = selected
        user.conversation_state = None
        return f"Sources mises a jour : {', '.join(selected)}\n\nTapez *Menu* pour continuer."

    user.conversation_state = None
    return MENU_MESSAGE


# ================================================
#  SUPPRESSION DE COMPTE
# ================================================

def _start_account_deletion(user: User) -> str:
    """Demarre le flux de suppression de compte."""
    user.conversation_state = "confirm_delete"
    return (
        "SUPPRESSION DE COMPTE\n\n"
        "Vous etes sur le point de supprimer votre compte Tendo.\n\n"
        "Cette action est irreversible. Toutes vos donnees seront supprimees :\n"
        "- Votre profil et preferences\n"
        "- Votre historique d'alertes\n"
        "- Votre abonnement\n\n"
        "Pour confirmer, tapez : *CONFIRMER SUPPRESSION*\n\n"
        "Tapez *Annuler* pour revenir en arriere."
    )


async def _handle_delete_confirmation(user: User, body: str, db: AsyncSession) -> str:
    """Gere la confirmation de suppression de compte."""
    text = body.strip().lower()

    if text in ("annuler", "cancel", "non"):
        user.conversation_state = None
        return "Suppression annulee. Votre compte est intact.\n\nTapez *Menu* pour continuer."

    if text == "confirmer suppression":
        # Desactiver le compte et nettoyer les donnees
        user.is_active = False
        user.name = ""
        user.company = None
        user.sectors = []
        user.regions = []
        user.preferred_sources = []
        user.conversation_state = None
        user.conversation_data = None
        user.subscription_status = SubscriptionStatus.CANCELED.value
        flag_modified(user, "conversation_data")

        logger.info(f"[Suppression] Compte desactive: user_id={user.id}, phone={user.phone_number}")

        return (
            "COMPTE SUPPRIME\n\n"
            "Votre compte Tendo a ete supprime.\n"
            "Vos donnees personnelles ont ete effacees.\n\n"
            "Nous sommes desoles de vous voir partir.\n"
            "Si vous changez d'avis, envoyez-nous un message pour vous reinscrire."
        )

    return "Pour confirmer la suppression, tapez exactement : *CONFIRMER SUPPRESSION*\n\nOu tapez *Annuler*."


# ================================================
#  HISTORIQUE & PAIEMENT
# ================================================

async def _get_history(user: User, db: AsyncSession) -> str:
    """Retourne l'historique des notifications de l'utilisateur."""
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.sent_at.desc())
        .limit(5)
    )
    notifications = result.scalars().all()

    if not notifications:
        return "Aucune alerte recente.\n\nVos alertes personnalisees arriveront bientot."

    lines = ["VOS 5 DERNIERES ALERTES\n"]
    for i, notif in enumerate(notifications, 1):
        pub_result = await db.execute(
            select(Publication).where(Publication.id == notif.publication_id)
        )
        pub = pub_result.scalar_one_or_none()
        if pub:
            date_str = notif.sent_at.strftime("%d/%m/%Y")
            lines.append(f"{i}. [{date_str}] {pub.title[:60]}")
            lines.append(f"   Ref: {pub.reference}")
            lines.append("")

    return "\n".join(lines)


async def _handle_payment(user: User, plan: str = "essentiel") -> str:
    """Genere un lien de paiement FedaPay."""
    plan_names = {"essentiel": "Essentiel", "premium": "Premium"}
    plan_label = plan_names.get(plan, "Essentiel")

    try:
        result = await create_payment_link(
            user_phone=user.phone_number,
            plan=plan,
            user_name=user.name,
        )
        other_plan = "Premium (15 000 FCFA)" if plan == "essentiel" else "Essentiel (5 000 FCFA)"
        other_cmd = "Premium" if plan == "essentiel" else "Essentiel"

        return (
            f"PAIEMENT TENDO\n\n"
            f"Plan : {plan_label}\n"
            f"Montant : {result['amount']:,.0f} FCFA\n\n"
            f"Cliquez pour payer (Mobile Money MTN/Moov) :\n"
            f"{result['payment_link']}\n\n"
            f"Pour le plan {other_plan}, tapez *{other_cmd}*."
        )
    except Exception as e:
        logger.error(f"Erreur paiement: {e}")
        return (
            "Une erreur est survenue lors de la creation du paiement.\n\n"
            "Veuillez reessayer dans quelques instants ou contactez le support."
        )


async def _handle_button_voir(body: str, from_number: str, db: AsyncSession):
    """Bouton 'Voir le dossier' — envoie le PDF ou le lien de l'avis."""
    pub_id = body.replace("voir_", "").strip()
    result = await db.execute(
        select(Publication).where(Publication.id == int(pub_id))
    )
    pub = result.scalar_one_or_none()

    if not pub:
        await whatsapp.send_message(from_number, "Publication introuvable.")
        return

    if pub.pdf_url:
        await whatsapp.send_document(
            from_number,
            document_url=pub.pdf_url,
            caption=f"{pub.title}\nRef: {pub.reference}",
            filename=f"{pub.reference}.pdf",
        )
    else:
        await whatsapp.send_message(
            from_number,
            f"Aucun document PDF disponible pour cet avis.\n\n"
            f"*{pub.title}*\nRef: {pub.reference}\n\n"
            f"Tapez */analyser {pub.reference}* pour voir les details disponibles.",
        )


async def _handle_button_savoir(body: str, from_number: str, user: User, db: AsyncSession):
    """Bouton 'En savoir plus' — analyse IA de la publication."""
    pub_id = body.replace("savoir_", "").strip()
    result = await db.execute(
        select(Publication).where(Publication.id == int(pub_id))
    )
    pub = result.scalar_one_or_none()

    if not pub:
        await whatsapp.send_message(from_number, "Publication introuvable.")
        return

    # Utiliser l'analyse IA existante
    reply = await _handle_document_analysis(f"/analyser {pub.reference}", user, db)
    await whatsapp.send_message(from_number, reply)


async def _handle_button_demander(body: str, from_number: str, user: User, db: AsyncSession):
    """Bouton 'Demander dossier' — envoie email a l'autorite contractante."""
    pub_id = body.replace("demander_", "").strip()
    result = await db.execute(
        select(Publication).where(Publication.id == int(pub_id))
    )
    pub = result.scalar_one_or_none()

    if not pub:
        await whatsapp.send_message(from_number, "Publication introuvable.")
        return

    reply = await _handle_dossier_request(
        f"/demander_dossier {pub.reference}", user, db
    )
    await whatsapp.send_message(from_number, reply)


async def _handle_dossier_request(body: str, user: User, db: AsyncSession) -> str:
    """Gere la demande de dossier d'AO."""
    ref = body.replace("/demander_dossier", "").strip()
    if not ref:
        return (
            "Veuillez preciser la reference de l'appel d'offres.\n\n"
            "Exemple : /demander_dossier AO-MARC-12345678"
        )

    result = await db.execute(
        select(Publication).where(Publication.reference == ref)
    )
    pub = result.scalar_one_or_none()

    if not pub:
        return f"Publication '{ref}' non trouvee.\n\nVerifiez la reference."

    if not pub.authority_email:
        return (
            f"{pub.title}\n\n"
            "L'adresse email de l'autorite contractante n'est pas disponible.\n\n"
            "Veuillez nous communiquer l'email de l'autorite et nous ferons la demande pour vous."
        )

    if user.subscription_plan != "premium" and user.subscription_status != SubscriptionStatus.TRIAL.value:
        return (
            "La demande automatique de dossier est reservee au Plan Premium.\n\n"
            "Tapez *Abonnement* pour voir les options."
        )

    result = await send_dossier_request(
        authority_email=pub.authority_email,
        publication_reference=pub.reference,
        publication_title=pub.title,
        requester_name=user.name or user.phone_number,
        requester_company=user.company,
        cc_email=user.email_address,
    )

    if result["success"]:
        tracking = EmailTracking(
            user_id=user.id,
            publication_id=pub.id,
            email_sent_to=pub.authority_email,
            subject=result["subject"],
        )
        db.add(tracking)

        return (
            "DEMANDE ENVOYEE\n\n"
            f"{pub.title}\n"
            f"Email envoye a : {pub.authority_email}\n\n"
            "Nous vous notifierons des reception de la reponse."
        )
    else:
        return f"Erreur lors de l'envoi : {result.get('error', 'Inconnue')}"


# ================================================
#  ANALYSE IA DE DOCUMENTS
# ================================================

async def _handle_document_analysis(body: str, user: User, db: AsyncSession) -> str:
    """Analyse un appel d'offres avec l'IA (PDF + contenu)."""
    import re
    from app.services.document_analyzer import analyze_publication, build_publication_context

    # Extraire la reference du message
    # Supporte: /analyser AO-MARC-12345, analyse AO-MARC-12345, "detail de AO-MARC-12345"
    ref_match = re.search(r"(AO-[A-Z]+-[a-fA-F0-9]+)", body, re.IGNORECASE)
    ref_text = body.replace("/analyser", "").strip()

    pub = None

    if ref_match:
        ref = ref_match.group(1)
        result = await db.execute(
            select(Publication).where(Publication.reference == ref)
        )
        pub = result.scalar_one_or_none()

    if not pub and ref_text:
        # Essayer une recherche par texte dans le titre
        result = await db.execute(
            select(Publication)
            .where(Publication.title.ilike(f"%{ref_text[:50]}%"))
            .limit(1)
        )
        pub = result.scalar_one_or_none()

    if not pub:
        return (
            "Publication non trouvee.\n\n"
            "Utilisez la reference exacte :\n"
            "/analyser AO-MARC-12345678\n\n"
            "Vous pouvez trouver les references dans vos alertes."
        )

    # Verifier les droits (premium ou trial)
    if user.subscription_status not in (
        "trial", "active"
    ) and user.subscription_plan != "premium":
        return (
            "L'analyse detaillee des documents est disponible "
            "pendant votre essai gratuit ou avec un abonnement.\n\n"
            "Tapez *Abonnement* pour voir les plans."
        )

    await whatsapp.send_message(
        user.phone_number,
        f"Analyse en cours de :\n*{pub.title[:80]}*\n\nPatientez quelques secondes..."
    )

    try:
        # Construire le contexte complet (inclut PDF si disponible)
        context = await build_publication_context(pub)

        # Extraire la question specifique s'il y en a une
        user_question = ""
        question_markers = ("?", "comment", "quand", "combien", "qui", "quel", "quelle")
        for marker in question_markers:
            if marker in body.lower():
                # L'utilisateur pose une question specifique
                user_question = body.replace("/analyser", "").strip()
                # Enlever la reference de la question
                if ref_match:
                    user_question = user_question.replace(ref_match.group(1), "").strip()
                break

        analysis = await analyze_publication(
            title=pub.title,
            summary=pub.summary or "",
            html_content=pub.html_content or "",
            pdf_text=context,
            user_question=user_question,
        )

        # Si le PDF existe, envoyer aussi le document
        if pub.pdf_url:
            try:
                await whatsapp.send_document(
                    user.phone_number,
                    document_url=pub.pdf_url,
                    caption=f"Document : {pub.title[:60]}",
                    filename=f"{pub.reference}.pdf",
                )
            except Exception:
                pass  # Le PDF n'est pas critique

        return analysis

    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur analyse: {e}")
        return (
            f"ANALYSE -- {pub.title[:60]}\n\n"
            f"Source : {pub.source}\n"
            f"Reference : {pub.reference}\n"
            f"{'Budget : ' + str(pub.budget) + ' FCFA' if pub.budget else ''}\n"
            f"{'Date limite : ' + pub.deadline.strftime('%d/%m/%Y') if pub.deadline else ''}\n\n"
            f"{pub.summary or 'Pas de resume disponible.'}\n\n"
            f"{'Document : ' + pub.pdf_url if pub.pdf_url else ''}"
        )


# ================================================
#  TRAITEMENT DES DOCUMENTS ET IMAGES WHATSAPP
# ================================================

async def _process_document(
    from_number: str,
    media_id: str,
    filename: str,
    mime_type: str,
    caption: str,
    db: AsyncSession,
):
    """Traite un document envoye via WhatsApp (PDF, Word, etc.)."""
    from app.models.document_analysis import DocumentAnalysis

    logger.info(f"[Webhook] Document recu de {from_number}: {filename} ({mime_type})")

    # Recuperer l'utilisateur
    result = await db.execute(select(User).where(User.phone_number == from_number))
    user = result.scalar_one_or_none()
    if not user:
        await whatsapp.send_message(from_number, "Veuillez d'abord vous inscrire. Tapez *Menu*.")
        return

    # Informer l'utilisateur que l'analyse est en cours
    await whatsapp.send_message(
        from_number,
        f"Document recu : *{filename}*\n\nAnalyse en cours, patientez quelques secondes..."
    )

    try:
        # 1. Telecharger le media depuis WhatsApp
        file_bytes = await download_whatsapp_media(media_id)
        if not file_bytes:
            await whatsapp.send_message(
                from_number,
                "Erreur lors du telechargement du document. Veuillez reessayer."
            )
            return

        # 2. Extraire le texte
        if "pdf" in mime_type.lower() or filename.lower().endswith(".pdf"):
            text, method, page_count = await extract_pdf_text(pdf_bytes=file_bytes)
        else:
            # Pour les autres formats, essayer quand meme
            text, method, page_count = await extract_pdf_text(pdf_bytes=file_bytes)

        if not text or len(text.strip()) < 50:
            await whatsapp.send_message(
                from_number,
                "Je n'ai pas pu extraire suffisamment de texte de ce document.\n\n"
                "Assurez-vous que le fichier est un PDF lisible (non corrompu).\n"
                "Si c'est un document scanne, essayez d'envoyer une photo plus nette."
            )
            return

        # 3. Classifier le document
        classification = classify_document(text)

        # Si confiance faible, enrichir avec l'IA
        if classification["confidence"] < 0.5:
            classification = await classify_with_ai(text, classification)

        # 4. Sauvegarder l'analyse en BDD
        doc_analysis = DocumentAnalysis(
            source_type="whatsapp",
            original_filename=filename,
            extracted_text=text[:50000],  # Limiter la taille
            extraction_method=method,
            page_count=page_count,
            char_count=len(text),
            document_type=classification["document_type"],
            document_subtype=classification.get("document_subtype"),
            classification_confidence=classification["confidence"],
            detected_sectors=classification.get("sectors", []),
            detected_regions=classification.get("regions", []),
            detected_country=classification.get("country"),
            is_analyzed=True,
        )
        db.add(doc_analysis)
        await db.flush()

        # 5. Analyser avec l'IA
        analysis = await analyze_document_full(
            text=text,
            classification=classification,
            user_sectors=user.sectors or [],
            user_regions=user.regions or [],
            user_question=caption,
        )

        # 6. Sauvegarder le resume IA
        doc_analysis.ai_summary = analysis[:2000]
        await db.commit()

        # 7. Verifier la pertinence pour l'utilisateur
        relevance = check_relevance_for_user(
            classification, user.sectors or [], user.regions or [],
        )

        # 8. Construire la reponse
        header = (
            f"ANALYSE DE DOCUMENT\n"
            f"Type : *{classification.get('label', 'Non classifie')}*\n"
            f"Confiance : {int(classification['confidence'] * 100)}%\n"
        )
        if relevance["is_relevant"]:
            header += f"Pertinence : Ce document vous concerne ({relevance['reason']})\n"

        header += "\n"
        full_reply = header + analysis

        # Envoyer (decouper si trop long pour WhatsApp — max ~4096 chars)
        if len(full_reply) > 4000:
            await whatsapp.send_message(from_number, full_reply[:4000])
            await whatsapp.send_message(from_number, "...(suite)\n\n" + full_reply[4000:8000])
        else:
            await whatsapp.send_message(from_number, full_reply)

    except Exception as e:
        logger.error(f"[Webhook] Erreur traitement document: {e}")
        await whatsapp.send_message(
            from_number,
            "Une erreur est survenue lors de l'analyse du document.\n"
            "Veuillez reessayer ou contacter le support."
        )


async def _process_image(
    from_number: str,
    media_id: str,
    mime_type: str,
    caption: str,
    db: AsyncSession,
):
    """Traite une image envoyee via WhatsApp (photo d'un document)."""
    from app.models.document_analysis import DocumentAnalysis

    logger.info(f"[Webhook] Image recue de {from_number} ({mime_type})")

    result = await db.execute(select(User).where(User.phone_number == from_number))
    user = result.scalar_one_or_none()
    if not user:
        await whatsapp.send_message(from_number, "Veuillez d'abord vous inscrire. Tapez *Menu*.")
        return

    await whatsapp.send_message(
        from_number,
        "Image recue. Extraction du texte en cours..."
    )

    try:
        # 1. Telecharger l'image
        image_bytes = await download_whatsapp_media(media_id)
        if not image_bytes:
            await whatsapp.send_message(from_number, "Erreur de telechargement. Reessayez.")
            return

        # 2. Extraire le texte de l'image
        text = await extract_image_text(image_bytes, mime_type)
        if not text or len(text.strip()) < 20:
            await whatsapp.send_message(
                from_number,
                "Je n'ai pas pu lire le texte de cette image.\n\n"
                "Conseils :\n"
                "- Prenez la photo bien a plat\n"
                "- Assurez un bon eclairage\n"
                "- Evitez les reflets\n"
                "- Envoyez plutot le fichier PDF si disponible"
            )
            return

        # 3. Classifier
        classification = classify_document(text)

        # 4. Sauvegarder
        doc_analysis = DocumentAnalysis(
            source_type="whatsapp",
            original_filename="photo_document.jpg",
            extracted_text=text[:50000],
            extraction_method="gemini_vision",
            char_count=len(text),
            document_type=classification["document_type"],
            document_subtype=classification.get("document_subtype"),
            classification_confidence=classification["confidence"],
            detected_sectors=classification.get("sectors", []),
            detected_regions=classification.get("regions", []),
            detected_country=classification.get("country"),
            is_analyzed=True,
        )
        db.add(doc_analysis)

        # 5. Analyser
        analysis = await analyze_document_full(
            text=text,
            classification=classification,
            user_sectors=user.sectors or [],
            user_regions=user.regions or [],
            user_question=caption,
        )

        doc_analysis.ai_summary = analysis[:2000]
        await db.commit()

        # 6. Repondre
        header = (
            f"ANALYSE D'IMAGE\n"
            f"Type detecte : *{classification.get('label', 'Non classifie')}*\n\n"
        )
        await whatsapp.send_message(from_number, header + analysis)

    except Exception as e:
        logger.error(f"[Webhook] Erreur traitement image: {e}")
        await whatsapp.send_message(
            from_number,
            "Erreur lors de l'analyse de l'image. Reessayez."
        )
