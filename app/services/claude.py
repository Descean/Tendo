"""Service IA Conversationnelle -- Tendo.

Architecture multi-fournisseur (cascade) :
1. Groq (Llama 3.3 70B, GRATUIT : 30 req/min) -> rapide, fiable
2. Google Gemini Flash (GRATUIT : 15 req/min) -> fallback
3. Claude (Anthropic) -> pour les abonnes premium uniquement
4. Fallback local -> si aucune API n'est disponible
"""

import asyncio
import re
from typing import Optional, List

import httpx

from app.config import settings
from app.utils.logger import logger

# Clients initialises paresseusement
_gemini_client = None
_claude_client = None


def _get_gemini():
    """Retourne le client Gemini (google-genai), ou None si non configure."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
        logger.info("[IA] Gemini Flash initialise")
        return _gemini_client
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

TON ROLE : Tu es un conseiller commercial professionnel. Tu accueilles les utilisateurs, reponds a leurs questions, et les guides vers les fonctionnalites.

PERSONNALITE :
- Professionnel, courtois, et direct
- Tu vouvoies toujours l'utilisateur
- Tu reponds en francais, de maniere claire et concise
- Tu ne mets JAMAIS d'emojis dans tes reponses
- Tu es competent sur les marches publics au Benin

FORMATAGE OBLIGATOIRE (messages WhatsApp) :
- Separe chaque idee par une LIGNE VIDE (double saut de ligne)
- Utilise *texte* pour mettre en gras les mots importants
- Fais des paragraphes courts (2-3 phrases maximum par paragraphe)
- Utilise des tirets (-) pour les listes
- Ne fais JAMAIS un bloc de texte compact sans espacement
- Maximum 5 paragraphes par reponse

OBJECTIFS :
1. Repondre aux questions de l'utilisateur de maniere utile
2. Si la question concerne les marches publics, donner une reponse pertinente
3. Montrer la valeur de Tendo quand c'est naturel (pas a chaque message)

CE QUE TENDO OFFRE :
- Alertes WhatsApp automatiques sur les appels d'offres
- Veille sur 7 sources : marches-publics.bj, ARMP, gouv.bj, ADPME, ABE, BAD, AFD
- Resumes intelligents des appels d'offres
- Demande automatique de dossiers d'AO (premium)
- Assistant IA expert en marches publics (premium)

ESSAI GRATUIT : 7 jours. Plan Essentiel : 5 000 FCFA/mois. Plan Premium : 15 000 FCFA/mois.

COMMANDES DISPONIBLES (guide l'utilisateur vers ces commandes si pertinent) :
- *Menu* : voir toutes les options
- *Abonnement* : voir les plans
- *Essentiel* ou *Premium* : souscrire directement
- *Historique* : voir les dernieres alertes
- *Profil* : modifier les preferences

REGLES STRICTES :
- Reponds toujours en francais
- AUCUN emoji, symbole, etoile decorative, fleche, coeur, etc.
- Ne fournis jamais de conseils juridiques formels
- Ne repete pas les memes formules d'accueil a chaque message
- Si l'utilisateur est deja inscrit, ne lui propose PAS de s'inscrire a nouveau"""


EXPERT_PROMPT = """Tu es Tendo, un assistant IA expert en marches publics et appels d'offres, specialise dans le contexte du Benin et de l'Afrique de l'Ouest, developpe par SHIFT UP.

Ton role :
- Repondre aux questions sur les procedures de passation des marches publics
- Expliquer les reglementations (Code des marches publics du Benin, directives UEMOA/CEDEAO)
- Aider a comprendre les documents d'appels d'offres (DAO, cahiers des charges)
- Conseiller sur la preparation des offres et soumissions
- Informer sur les opportunites de financement (BAD, AFD, Banque Mondiale, USAID)
- Expliquer les recours et contentieux en matiere de marches publics

FORMATAGE OBLIGATOIRE (messages WhatsApp) :
- Separe chaque idee par une LIGNE VIDE (double saut de ligne)
- Utilise *texte* pour mettre en gras les mots importants
- Fais des paragraphes courts (2-3 phrases maximum par paragraphe)
- Utilise des tirets (-) pour les listes
- Ne fais JAMAIS un bloc de texte compact sans espacement
- Maximum 8 paragraphes par reponse

Regles strictes :
- Reponds toujours en francais
- AUCUN emoji dans tes reponses
- Cite les textes reglementaires quand c'est pertinent
- Si tu n'es pas sur d'une information, dis-le clairement
- Ne fournis jamais de conseils juridiques formels, recommande de consulter un juriste"""


# ================================================
#  FORMATAGE POST-TRAITEMENT
# ================================================

def _format_for_whatsapp(text: str) -> str:
    """Formate la reponse IA pour un affichage propre sur WhatsApp.

    - Assure des lignes vides entre paragraphes
    - Convertit le markdown en format WhatsApp
    - Supprime les emojis restants
    - Nettoie les artefacts de formatage
    """
    if not text:
        return text

    # Supprimer les emojis (range Unicode)
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001f926-\U0001f937"  # supplemental
        "\U00010000-\U0010ffff"  # supplemental
        "\u2640-\u2642"
        "\u2600-\u2B55"
        "\u200d"
        "\u23cf"
        "\u23e9"
        "\u231a"
        "\ufe0f"
        "\u3030"
        "]+",
        flags=re.UNICODE,
    )
    text = emoji_pattern.sub("", text)

    # Convertir ## Titre en *Titre* (gras WhatsApp)
    text = re.sub(r"^#{1,3}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # Convertir **texte** en *texte* (WhatsApp utilise un seul asterisque)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)

    # Assurer des lignes vides entre paragraphes
    # Remplacer les sauts de ligne simples entre paragraphes par des doubles
    lines = text.split("\n")
    formatted_lines = []
    for i, line in enumerate(lines):
        formatted_lines.append(line)
        # Si la ligne actuelle n'est pas vide et la suivante non plus,
        # et la suivante ne commence pas par un tiret/numero (liste),
        # ajouter une ligne vide
        if (
            i < len(lines) - 1
            and line.strip()
            and lines[i + 1].strip()
            and not lines[i + 1].strip().startswith(("-", "*", "1", "2", "3", "4", "5", "6", "7", "8", "9"))
            and not line.strip().startswith(("-", "*"))
            and not line.strip().endswith(":")
        ):
            formatted_lines.append("")

    text = "\n".join(formatted_lines)

    # Nettoyer les lignes vides multiples (max 2 consecutives)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ================================================
#  FONCTIONS PRINCIPALES
# ================================================

async def chat(
    user_message: str,
    is_premium: bool = False,
    conversation_history: Optional[List[dict]] = None,
    publication_context: Optional[str] = None,
) -> str:
    """Envoie un message et retourne la reponse formatee pour WhatsApp.

    Cascade :
    - Premium -> Claude > Groq > Gemini > fallback
    - Non-premium -> Groq > Gemini > fallback
    """
    system_prompt = EXPERT_PROMPT if is_premium else COMMERCIAL_PROMPT
    if publication_context:
        system_prompt += f"\n\nContexte de la publication referencee :\n{publication_context}"

    # Premium : essayer Claude d'abord
    if is_premium:
        result = await _chat_claude(user_message, system_prompt, conversation_history)
        if result:
            return _format_for_whatsapp(result)

    # Essayer Groq (gratuit, rapide)
    result = await _chat_groq(user_message, system_prompt, conversation_history)
    if result:
        return _format_for_whatsapp(result)

    # Essayer Gemini (gratuit)
    result = await _chat_gemini(user_message, system_prompt, conversation_history)
    if result:
        return _format_for_whatsapp(result)

    # Dernier recours : fallback local
    return _fallback_chat(user_message, is_premium)


# ================================================
#  GROQ (Llama 3.3 70B — GRATUIT, 30 req/min)
# ================================================

async def _chat_groq(
    user_message: str,
    system_prompt: str,
    conversation_history: Optional[List[dict]] = None,
) -> Optional[str]:
    """Chat via Groq API (compatible OpenAI). Gratuit, tres rapide."""
    if not settings.groq_api_key:
        return None

    messages = [{"role": "system", "content": system_prompt}]
    if conversation_history:
        messages.extend(conversation_history[-6:])
    messages.append({"role": "user", "content": user_message})

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.7,
                },
            )
            data = response.json()

        if response.status_code == 200:
            reply = data["choices"][0]["message"]["content"].strip()
            logger.info(f"[Groq] Reponse: {len(reply)} caracteres")
            return reply
        else:
            logger.error(f"[Groq] Erreur {response.status_code}: {data}")
            return None

    except Exception as e:
        logger.error(f"[Groq] Erreur: {e}")
        return None


# ================================================
#  GEMINI (gratuit, fallback) — appel async correct
# ================================================

async def _chat_gemini(
    user_message: str,
    system_prompt: str,
    conversation_history: Optional[List[dict]] = None,
) -> Optional[str]:
    """Chat via Google Gemini (gratuit).

    Utilise asyncio.to_thread() pour ne pas bloquer l'event loop
    car google-genai fait des appels HTTP synchrones.
    """
    client = _get_gemini()
    if client is None:
        return None

    def _sync_gemini_call():
        """Appel synchrone Gemini execute dans un thread separe."""
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
            model="gemini-2.0-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=500,
                temperature=0.7,
            ),
        )
        return response.text.strip()

    try:
        reply = await asyncio.to_thread(_sync_gemini_call)
        logger.info(f"[Gemini] Reponse: {len(reply)} caracteres")
        return reply
    except Exception as e:
        logger.error(f"[Gemini] Erreur: {e}")
        return None


# ================================================
#  CLAUDE (premium uniquement)
# ================================================

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


# ================================================
#  RESUME DE PUBLICATIONS
# ================================================

async def summarize_publication(title: str, content: str) -> str:
    """Resume un appel d'offres pour l'alerte WhatsApp."""
    prompt = f"""Resume cet appel d'offres en 3-4 lignes maximum pour un message WhatsApp.
Inclus : objet, secteur, deadline si disponible, budget si mentionne.
Ne mets aucun emoji. Separe les informations par des sauts de ligne.

Titre : {title}
Contenu : {content[:3000]}"""

    system = "Tu resumes des appels d'offres de maniere concise pour des alertes WhatsApp. Reponds en francais. Aucun emoji. Separe chaque information par un saut de ligne."

    # Essayer Groq d'abord
    result = await _chat_groq(prompt, system)
    if result:
        return _format_for_whatsapp(result)

    # Essayer Gemini
    result = await _chat_gemini(prompt, system)
    if result:
        return _format_for_whatsapp(result)

    # Fallback Claude
    client = _get_claude()
    if client:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return _format_for_whatsapp(response.content[0].text)
        except Exception as e:
            logger.error(f"[Claude] Erreur resume: {e}")

    summary = content[:200].strip()
    return f"{summary}..." if len(content) > 200 else summary or title


# ================================================
#  DETECTION D'INTENTION (locale, gratuite)
# ================================================

async def detect_intent(message: str) -> dict:
    """Detecte l'intention d'un message utilisateur (local uniquement)."""
    local_intent = _simple_intent_detection(message)
    return {"intent": local_intent, "raw_message": message}


def _simple_intent_detection(message: str) -> str:
    """Detection d'intention par mots-cles.

    Detecte les commandes dans les phrases completes, pas seulement les mots exacts.
    Exemple: "Je veux payer" -> PAIEMENT, "Souscrire au plan essentiel" -> PAIEMENT.
    """
    msg = message.lower().strip()

    # Commandes numeriques du menu (match exact uniquement)
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

    # Navigation
    if msg in ("menu", "aide", "help", "accueil", "start"):
        return "MENU"

    # Demande de dossier (priorite haute)
    if "/demander_dossier" in msg or "demander le dossier" in msg or "demander un dossier" in msg:
        return "DEMANDE_DOSSIER"

    # Suppression de compte
    if any(w in msg for w in ("supprimer mon compte", "supprimer mes donnees",
                              "effacer mon compte", "delete", "suppression")):
        return "SUPPRIMER_COMPTE"

    # Modification de profil
    if any(w in msg for w in ("modifier mon profil", "modifier mes preferences",
                              "changer mes", "mettre a jour", "modifier profil")):
        return "MODIFIER_PROFIL"

    # Paiement (avant inscription car "souscrire" pourrait etre confondu)
    paiement_words = ("paiement", "payer", "souscrire", "premium", "essentiel",
                      "upgrade", "lien de paiement", "mobile money", "5000", "15000",
                      "5 000", "15 000", "faire pour")
    if any(w in msg for w in paiement_words):
        return "PAIEMENT"

    # Inscription (mais pas si deja dans un contexte de paiement)
    if any(w in msg for w in ("inscription", "inscrire", "register", "m'inscrire")):
        if any(w in msg for w in ("viens de", "deja inscrit", "deja fait")):
            return "QUESTION"
        return "INSCRIPTION"

    # Preferences / profil
    if any(w in msg for w in ("profil", "preferences")):
        return "MODIFIER_PROFIL"

    # Abonnement / plans
    if any(w in msg for w in ("abonnement", "plans", "plan", "tarif", "tarifs", "prix",
                              "nos offres", "formules")):
        return "ABONNEMENT"

    # Historique
    if any(w in msg for w in ("historique", "alertes", "notifications", "mes alertes",
                              "dernieres alertes")):
        return "HISTORIQUE"

    # Support
    if any(w in msg for w in ("support", "aide humaine", "agent", "probleme",
                              "reclamation", "contacter")):
        return "SUPPORT"

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
        "Je peux vous aider a trouver des appels d'offres au Benin.\n\n"
        "Tapez *Menu* pour voir les options disponibles."
    )
