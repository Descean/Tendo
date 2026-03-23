"""Service IA Conversationnelle – Tendo.

Architecture multi-fournisseur :
1. Google Gemini Flash (GRATUIT : 15 req/min, 1M tokens/jour) → par défaut
2. Claude (Anthropic) → pour les abonnés premium (meilleure qualité)
3. Fallback local → si aucune API n'est disponible

Le bot a 2 modes :
- Mode COMMERCIAL (nouveaux utilisateurs / trial) : conversationnel, engageant
- Mode EXPERT (abonnés premium) : assistant professionnel marchés publics
"""

from typing import Optional, List

from app.config import settings
from app.utils.logger import logger

# Clients initialisés paresseusement
_gemini_model = None
_claude_client = None


def _get_gemini():
    """Retourne le client Gemini (google-genai), ou None si non configuré."""
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai
        _gemini_model = genai.Client(api_key=settings.gemini_api_key)
        logger.info("[IA] Gemini Flash initialisé (gratuit)")
        return _gemini_model
    except Exception as e:
        logger.error(f"[IA] Erreur init Gemini: {e}")
        return None


def _get_claude():
    """Retourne le client Anthropic, ou None si non configuré."""
    global _claude_client
    if _claude_client is not None:
        return _claude_client
    if not settings.claude_api_key or settings.claude_api_key.startswith("sk-ant-xxx"):
        return None
    try:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=settings.claude_api_key)
        logger.info("[IA] Claude (Anthropic) initialisé")
        return _claude_client
    except Exception as e:
        logger.error(f"[IA] Erreur init Claude: {e}")
        return None


# ═══════════════════════════════════════════
#  SYSTEM PROMPTS
# ═══════════════════════════════════════════

COMMERCIAL_PROMPT = """Tu es Tendo, l'assistant intelligent de veille sur les marchés publics au Bénin et en Afrique de l'Ouest.

TON RÔLE : Tu es comme un agent commercial sympathique et professionnel. Tu accueilles les nouveaux utilisateurs chaleureusement et tu les guides pour s'inscrire.

PERSONNALITÉ :
- Chaleureux, professionnel mais accessible
- Tu utilises un langage simple, adapté au contexte béninois
- Tu tutoies/vouvoies selon le contexte (préfère le vouvoiement)
- Tu es enthousiaste quand tu parles des avantages de Tendo
- Tu réponds en français

OBJECTIFS :
1. Engager la conversation naturellement
2. Comprendre les besoins de l'utilisateur (secteur, zone, type de marchés)
3. Montrer la valeur de Tendo (alertes automatiques, gain de temps, etc.)
4. Guider vers l'inscription (tapez *1* ou *Inscription*)

CE QUE TENDO OFFRE :
- Alertes WhatsApp automatiques sur les appels d'offres
- Veille sur 6+ sources (marches-publics.bj, ARMP, gouv.bj, ADPME, ABE, etc.)
- Résumés intelligents des appels d'offres
- Demande automatique de dossiers d'AO (premium)
- Assistant IA expert en marchés publics (premium)

ESSAI GRATUIT : 7 jours pour tester toutes les fonctionnalités.
Plan Essentiel : 5 000 FCFA/mois | Plan Premium : 15 000 FCFA/mois

RÈGLES :
- Réponds toujours en français
- Messages courts (WhatsApp) — max 3-4 phrases
- Si l'utilisateur pose une question technique sur les marchés publics, réponds brièvement et mentionne que l'assistant premium donne des réponses plus détaillées
- Ne fournis JAMAIS de conseils juridiques formels"""


EXPERT_PROMPT = """Tu es Tendo, un assistant IA expert en marchés publics et appels d'offres, spécialisé dans le contexte du Bénin et de l'Afrique de l'Ouest.

Ton rôle :
- Répondre aux questions sur les procédures de passation des marchés publics
- Expliquer les réglementations (Code des marchés publics du Bénin, directives UEMOA/CEDEAO)
- Aider à comprendre les documents d'appels d'offres (DAO, cahiers des charges)
- Conseiller sur la préparation des offres et soumissions
- Informer sur les opportunités de financement (BAD, AFD, Banque Mondiale, USAID)
- Expliquer les recours et contentieux en matière de marchés publics

Règles :
- Réponds toujours en français
- Sois concis (messages WhatsApp) mais précis
- Cite les textes réglementaires quand c'est pertinent
- Si tu n'es pas sûr, dis-le clairement
- Ne fournis JAMAIS de conseils juridiques formels, recommande de consulter un juriste
- Utilise des emojis avec modération pour rester professionnel"""


# ═══════════════════════════════════════════
#  FONCTIONS PRINCIPALES
# ═══════════════════════════════════════════

async def chat(
    user_message: str,
    is_premium: bool = False,
    conversation_history: Optional[List[dict]] = None,
    publication_context: Optional[str] = None,
) -> str:
    """Envoie un message et retourne la réponse.

    - Premium → Claude (meilleure qualité) ou Gemini en fallback
    - Non-premium → Gemini (gratuit) ou fallback local
    """
    system_prompt = EXPERT_PROMPT if is_premium else COMMERCIAL_PROMPT
    if publication_context:
        system_prompt += f"\n\nContexte de la publication référencée :\n{publication_context}"

    # Premium : essayer Claude d'abord
    if is_premium:
        result = await _chat_claude(user_message, system_prompt, conversation_history)
        if result:
            return result

    # Essayer Gemini (gratuit)
    result = await _chat_gemini(user_message, system_prompt, conversation_history)
    if result:
        return result

    # Dernier recours : fallback local
    return _fallback_chat(user_message, is_premium)


async def _chat_gemini(
    user_message: str,
    system_prompt: str,
    conversation_history: Optional[List[dict]] = None,
) -> Optional[str]:
    """Chat via Google Gemini (gratuit) — nouveau SDK google-genai."""
    client = _get_gemini()
    if client is None:
        return None

    try:
        from google.genai import types

        # Construire l'historique pour Gemini
        contents = []
        if conversation_history:
            for msg in conversation_history[-6:]:
                role = "user" if msg["role"] == "user" else "model"
                contents.append(types.Content(
                    role=role,
                    parts=[types.Part(text=msg["content"])],
                ))

        contents.append(types.Content(
            role="user",
            parts=[types.Part(text=user_message)],
        ))

        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=500,
                temperature=0.7,
            ),
        )

        reply = response.text.strip()
        logger.info(f"[Gemini] Réponse: {len(reply)} caractères")
        return reply

    except Exception as e:
        logger.error(f"[Gemini] Erreur: {e}")
        return None


async def _chat_claude(
    user_message: str,
    system_prompt: str,
    conversation_history: Optional[List[dict]] = None,
) -> Optional[str]:
    """Chat via Claude (Anthropic) — pour les premium."""
    client = _get_claude()
    if client is None:
        return None

    messages = []
    if conversation_history:
        messages.extend(conversation_history[-10:])
    messages.append({"role": "user", "content": user_message})

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
        reply = response.content[0].text
        logger.info(f"[Claude] Réponse: {len(reply)} caractères")
        return reply
    except Exception as e:
        logger.error(f"[Claude] Erreur: {e}")
        return None


async def summarize_publication(title: str, content: str) -> str:
    """Résume un appel d'offres pour l'alerte WhatsApp."""
    prompt = f"""Résume cet appel d'offres en 3-4 lignes maximum pour un message WhatsApp.
Inclus : objet, secteur, deadline si disponible, budget si mentionné.

Titre : {title}
Contenu : {content[:3000]}"""

    # Essayer Gemini d'abord (gratuit)
    client = _get_gemini()
    if client:
        try:
            from google.genai import types
            response = client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="Tu résumes des appels d'offres de manière concise pour des alertes WhatsApp. Réponds en français.",
                    max_output_tokens=300,
                    temperature=0.3,
                ),
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[Gemini] Erreur résumé: {e}")

    # Fallback Claude
    client = _get_claude()
    if client:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system="Tu résumes des appels d'offres de manière concise pour des alertes WhatsApp. Réponds en français.",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"[Claude] Erreur résumé: {e}")

    # Fallback simple
    summary = content[:200].strip()
    return f"{summary}..." if len(content) > 200 else summary or title


async def detect_intent(message: str) -> dict:
    """Détecte l'intention d'un message utilisateur.

    Stratégie optimisée :
    1. D'abord détection locale (gratuit, instantané)
    2. Si c'est un raccourci clair → retour immédiat (pas d'appel API)
    3. Seulement pour "QUESTION" → pas d'appel API (économie totale)
    """
    local_intent = _simple_intent_detection(message)
    return {"intent": local_intent, "raw_message": message}


# ═══════════════════════════════════════════
#  DÉTECTION LOCALE (100% gratuite)
# ═══════════════════════════════════════════

def _simple_intent_detection(message: str) -> str:
    """Détection d'intention simple par mots-clés (fallback).

    Couvre les raccourcis numériques du menu, les mots-clés courants
    et les variantes françaises / franglais courantes au Bénin.
    """
    msg = message.lower().strip()

    # ── Raccourcis numériques du menu ──
    if msg in ("1", "01"):
        return "INSCRIPTION"
    if msg in ("2", "02"):
        return "ABONNEMENT"
    if msg in ("3", "03"):
        return "HISTORIQUE"
    if msg in ("4", "04"):
        return "PAIEMENT"
    if msg in ("5", "05"):
        return "SUPPORT"

    # ── Mots-clés explicites ──
    if msg in ("menu", "aide", "help", "accueil", "start", "bonjour", "salut", "hello", "hi"):
        return "MENU"
    if any(w in msg for w in ("inscription", "inscrire", "register", "profil", "préférences")):
        return "INSCRIPTION"
    if any(w in msg for w in ("abonnement", "plans", "plan", "tarif", "tarifs", "prix", "nos offres")):
        return "ABONNEMENT"
    if any(w in msg for w in ("historique", "alertes", "notifications", "mes alertes")):
        return "HISTORIQUE"
    if any(w in msg for w in ("paiement", "payer", "souscrire", "premium", "essentiel", "upgrade")):
        return "PAIEMENT"
    if any(w in msg for w in ("support", "aide humaine", "agent", "problème", "reclamation")):
        return "SUPPORT"
    if "/demander_dossier" in msg or "demander le dossier" in msg:
        return "DEMANDE_DOSSIER"
    return "QUESTION"


# ═══════════════════════════════════════════
#  FALLBACK LOCAL (pas d'API)
# ═══════════════════════════════════════════

def _fallback_chat(message: str, is_premium: bool = False) -> str:
    """Réponse locale quand aucune IA n'est disponible."""
    msg = message.lower()

    if any(w in msg for w in ["appel d'offres", "ao", "marché public", "soumission"]):
        return (
            "📋 *Marchés Publics au Bénin*\n\n"
            "Les appels d'offres sont publiés sur :\n"
            "• marches-publics.bj (portail national)\n"
            "• armp.bj (ARMP)\n"
            "• gouv.bj/opportunites\n\n"
            "Tapez *Abonnement* pour recevoir les alertes automatiques."
        )

    if any(w in msg for w in ["dao", "dossier", "cahier des charges"]):
        return (
            "📄 *Dossiers d'Appels d'Offres*\n\n"
            "Pour obtenir un DAO :\n"
            "1. Identifiez la référence de l'AO\n"
            "2. Tapez */demander_dossier REF*\n"
            "3. Nous enverrons la demande par email\n\n"
            "Cette fonctionnalité est disponible avec le *Plan Premium*."
        )

    return (
        "🤖 Je suis *Tendo*, votre assistant marchés publics.\n\n"
        "Je peux vous aider à trouver des appels d'offres au Bénin.\n"
        "Tapez *Menu* pour voir les options disponibles."
    )
