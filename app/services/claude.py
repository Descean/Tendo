"""Service IA Conversationnelle -- Tendo.

Architecture multi-fournisseur :
1. Google Gemini Flash (GRATUIT : 15 req/min, 1M tokens/jour) -> par defaut
2. Claude (Anthropic) -> pour les abonnes premium (meilleure qualite)
3. Fallback local -> si aucune API n'est disponible

Le bot a 2 modes :
- Mode COMMERCIAL (nouveaux utilisateurs / trial) : conversationnel, engageant
- Mode EXPERT (abonnes premium) : assistant professionnel marches publics
"""

from typing import Optional, List

from app.config import settings
from app.utils.logger import logger

# Clients initialises paresseusement
_gemini_model = None
_claude_client = None


def _get_gemini():
    """Retourne le client Gemini (google-genai), ou None si non configure."""
    global _gemini_model
    if _gemini_model is not None:
        return _gemini_model
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai
        _gemini_model = genai.Client(api_key=settings.gemini_api_key)
        logger.info("[IA] Gemini Flash initialise (gratuit)")
        return _gemini_model
    except Exception as e:
        logger.error(f"[IA] Erreur init Gemini: {e}")
        return None


def _get_claude():
    """Retourne le client Anthropic, ou None si non configure."""
    global _claude_client
    if _claude_client is not None:
        return _claude_client
    if not settings.claude_api_key or settings.claude_api_key.startswith("sk-ant-xxx"):
        return None
    try:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=settings.claude_api_key)
        logger.info("[IA] Claude (Anthropic) initialise")
        return _claude_client
    except Exception as e:
        logger.error(f"[IA] Erreur init Claude: {e}")
        return None


# ================================================
#  SYSTEM PROMPTS (sans emojis, professionnel)
# ================================================

COMMERCIAL_PROMPT = """Tu es Tendo, l'assistant de veille sur les marches publics au Benin et en Afrique de l'Ouest, developpe par SHIFT UP.

TON ROLE : Tu es un conseiller commercial professionnel. Tu accueilles les utilisateurs, reponds a leurs questions, et les guides vers l'inscription et l'abonnement.

PERSONNALITE :
- Professionnel, courtois, et direct
- Tu vouvoies toujours l'utilisateur
- Tu reponds en francais, de maniere claire et concise
- Tu ne mets JAMAIS d'emojis dans tes reponses
- Tu es competent sur les marches publics au Benin

OBJECTIFS :
1. Repondre aux questions de l'utilisateur de maniere utile
2. Si la question concerne les marches publics, donner une reponse pertinente
3. Montrer la valeur de Tendo quand c'est naturel (pas a chaque message)
4. Orienter vers l'inscription si l'utilisateur n'est pas encore inscrit (tapez 1 ou Inscription)

CE QUE TENDO OFFRE :
- Alertes WhatsApp automatiques sur les appels d'offres
- Veille sur 7 sources : marches-publics.bj, ARMP, gouv.bj, ADPME, ABE, BAD, AFD
- Resumes intelligents des appels d'offres
- Demande automatique de dossiers d'AO (premium)
- Assistant IA expert en marches publics (premium)

ESSAI GRATUIT : 7 jours. Plan Essentiel : 5 000 FCFA/mois. Plan Premium : 15 000 FCFA/mois.

REGLES STRICTES :
- Reponds toujours en francais
- Messages courts adaptes a WhatsApp (3 a 5 phrases maximum)
- AUCUN emoji : pas de symboles comme des etoiles, des coeurs, des fleches, etc.
- Si l'utilisateur pose une question technique pointue sur les marches publics, reponds brievement et mentionne que l'assistant premium offre des analyses plus approfondies
- Ne fournis jamais de conseils juridiques formels
- Ne repete pas les memes formules d'accueil a chaque message"""


EXPERT_PROMPT = """Tu es Tendo, un assistant IA expert en marches publics et appels d'offres, specialise dans le contexte du Benin et de l'Afrique de l'Ouest, developpe par SHIFT UP.

Ton role :
- Repondre aux questions sur les procedures de passation des marches publics
- Expliquer les reglementations (Code des marches publics du Benin, directives UEMOA/CEDEAO)
- Aider a comprendre les documents d'appels d'offres (DAO, cahiers des charges)
- Conseiller sur la preparation des offres et soumissions
- Informer sur les opportunites de financement (BAD, AFD, Banque Mondiale, USAID)
- Expliquer les recours et contentieux en matiere de marches publics

Regles strictes :
- Reponds toujours en francais
- Messages concis mais precis (adaptes a WhatsApp)
- AUCUN emoji dans tes reponses
- Cite les textes reglementaires quand c'est pertinent
- Si tu n'es pas sur d'une information, dis-le clairement
- Ne fournis jamais de conseils juridiques formels, recommande de consulter un juriste"""


# ================================================
#  FONCTIONS PRINCIPALES
# ================================================

async def chat(
    user_message: str,
    is_premium: bool = False,
    conversation_history: Optional[List[dict]] = None,
    publication_context: Optional[str] = None,
) -> str:
    """Envoie un message et retourne la reponse.

    - Premium -> Claude (meilleure qualite) ou Gemini en fallback
    - Non-premium -> Gemini (gratuit) ou fallback local
    """
    system_prompt = EXPERT_PROMPT if is_premium else COMMERCIAL_PROMPT
    if publication_context:
        system_prompt += f"\n\nContexte de la publication referencee :\n{publication_context}"

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
    """Chat via Google Gemini (gratuit) -- nouveau SDK google-genai."""
    client = _get_gemini()
    if client is None:
        return None

    try:
        from google.genai import types

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
        logger.info(f"[Gemini] Reponse: {len(reply)} caracteres")
        return reply

    except Exception as e:
        logger.error(f"[Gemini] Erreur: {e}")
        return None


async def _chat_claude(
    user_message: str,
    system_prompt: str,
    conversation_history: Optional[List[dict]] = None,
) -> Optional[str]:
    """Chat via Claude (Anthropic) -- pour les premium."""
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
        logger.info(f"[Claude] Reponse: {len(reply)} caracteres")
        return reply
    except Exception as e:
        logger.error(f"[Claude] Erreur: {e}")
        return None


async def summarize_publication(title: str, content: str) -> str:
    """Resume un appel d'offres pour l'alerte WhatsApp."""
    prompt = f"""Resume cet appel d'offres en 3-4 lignes maximum pour un message WhatsApp.
Inclus : objet, secteur, deadline si disponible, budget si mentionne.
Ne mets aucun emoji.

Titre : {title}
Contenu : {content[:3000]}"""

    client = _get_gemini()
    if client:
        try:
            from google.genai import types
            response = client.models.generate_content(
                model="gemini-2.0-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="Tu resumes des appels d'offres de maniere concise pour des alertes WhatsApp. Reponds en francais. Aucun emoji.",
                    max_output_tokens=300,
                    temperature=0.3,
                ),
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"[Gemini] Erreur resume: {e}")

    client = _get_claude()
    if client:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system="Tu resumes des appels d'offres de maniere concise pour des alertes WhatsApp. Reponds en francais. Aucun emoji.",
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            logger.error(f"[Claude] Erreur resume: {e}")

    summary = content[:200].strip()
    return f"{summary}..." if len(content) > 200 else summary or title


async def detect_intent(message: str) -> dict:
    """Detecte l'intention d'un message utilisateur (local uniquement)."""
    local_intent = _simple_intent_detection(message)
    return {"intent": local_intent, "raw_message": message}


# ================================================
#  DETECTION LOCALE (100% gratuite)
# ================================================

def _simple_intent_detection(message: str) -> str:
    """Detection d'intention par mots-cles pour les commandes explicites."""
    msg = message.lower().strip()

    # Raccourcis numeriques du menu
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

    # Mots-cles explicites
    if msg in ("menu", "aide", "help", "accueil", "start"):
        return "MENU"
    if any(w in msg for w in ("inscription", "inscrire", "register", "profil", "preferences")):
        return "INSCRIPTION"
    if any(w in msg for w in ("abonnement", "plans", "plan", "tarif", "tarifs", "prix", "nos offres")):
        return "ABONNEMENT"
    if any(w in msg for w in ("historique", "alertes", "notifications", "mes alertes")):
        return "HISTORIQUE"
    if any(w in msg for w in ("paiement", "payer", "souscrire", "premium", "essentiel", "upgrade")):
        return "PAIEMENT"
    if any(w in msg for w in ("support", "aide humaine", "agent", "probleme", "reclamation")):
        return "SUPPORT"
    if "/demander_dossier" in msg or "demander le dossier" in msg:
        return "DEMANDE_DOSSIER"

    return "QUESTION"


# ================================================
#  FALLBACK LOCAL (pas d'API)
# ================================================

def _fallback_chat(message: str, is_premium: bool = False) -> str:
    """Reponse locale quand aucune IA n'est disponible."""
    msg = message.lower()

    if any(w in msg for w in ["appel d'offres", "ao", "marche public", "soumission"]):
        return (
            "MARCHES PUBLICS AU BENIN\n\n"
            "Les appels d'offres sont publies sur :\n"
            "- marches-publics.bj (portail national)\n"
            "- armp.bj (ARMP)\n"
            "- gouv.bj/opportunites\n\n"
            "Tapez *Abonnement* pour recevoir les alertes automatiques."
        )

    if any(w in msg for w in ["dao", "dossier", "cahier des charges"]):
        return (
            "DOSSIERS D'APPELS D'OFFRES\n\n"
            "Pour obtenir un DAO :\n"
            "1. Identifiez la reference de l'AO\n"
            "2. Tapez /demander_dossier REF\n"
            "3. Nous enverrons la demande par email\n\n"
            "Cette fonctionnalite est disponible avec le Plan Premium."
        )

    return (
        "Je suis Tendo, votre assistant marches publics.\n\n"
        "Je peux vous aider a trouver des appels d'offres au Benin.\n"
        "Tapez *Menu* pour voir les options disponibles."
    )
