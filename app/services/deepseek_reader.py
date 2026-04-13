"""Service de lecture intelligente de documents via DeepSeek.

DeepSeek est utilise pour extraire les informations structurees des PDF
d'appels d'offres : autorite contractante, objet, reference, type de marche,
montant garantie, date depot, etc.

Fallback: Groq > Gemini si DeepSeek indisponible.
"""

from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger


EXTRACTION_PROMPT = """Tu es un expert en marches publics. Analyse ce document et extrais les informations suivantes au format structure.
Si une information n'est pas disponible, ecris "N/A".

Reponds UNIQUEMENT avec les champs ci-dessous, un par ligne, au format "Champ: valeur" :

Autorite_contractante: [nom de l'autorite contractante / maitre d'ouvrage]
Objet: [objet du marche, description courte]
Reference: [reference du marche / numero d'avis]
Type_marche: [Travaux / Fournitures / Services / Prestations intellectuelles]
Budget: [montant estime en FCFA, chiffre uniquement]
Garantie_soumission: [montant de la garantie de soumission en FCFA]
Date_depot: [date limite de depot des offres, format JJ/MM/AAAA]
Heure_depot: [heure limite de depot]
Lieu_depot: [lieu de depot des offres]
Lieu_ouverture: [lieu d'ouverture des plis]
Source_financement: [source de financement : BN, BAD, BM, UE, AFD, etc.]
Lots: [nombre de lots si marche alloti]
Conditions_participation: [conditions principales de participation]
Documents_requis: [documents a fournir avec l'offre]
Contact_email: [email de contact de l'autorite]
Resume: [resume en 3-4 phrases du contenu du document]

DOCUMENT :
{content}"""


async def extract_document_info(text_content: str) -> Optional[dict]:
    """Extrait les informations structurees d'un document via IA.

    Cascade: DeepSeek > Groq > Gemini
    """
    if not text_content or len(text_content.strip()) < 50:
        return None

    # DeepSeek supporte 64K tokens (~200K chars). On envoie jusqu'a 30000 chars
    content = text_content[:30000]
    prompt = EXTRACTION_PROMPT.format(content=content)

    # Essayer DeepSeek
    result = await _call_deepseek(prompt)
    if result:
        return _parse_extraction(result)

    # Fallback Groq
    result = await _call_groq(prompt)
    if result:
        return _parse_extraction(result)

    # Fallback Gemini
    result = await _call_gemini(prompt)
    if result:
        return _parse_extraction(result)

    return None


async def _call_deepseek(prompt: str) -> Optional[str]:
    """Appelle l'API DeepSeek (compatible OpenAI)."""
    if not settings.deepseek_api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "Tu es un expert en extraction d'informations de documents de marches publics. Reponds en francais."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.1,
                },
            )
            data = response.json()

        if response.status_code == 200:
            reply = data["choices"][0]["message"]["content"].strip()
            logger.info(f"[DeepSeek] Extraction: {len(reply)} chars")
            return reply
        else:
            logger.error(f"[DeepSeek] Erreur {response.status_code}: {data}")
            return None

    except Exception as e:
        logger.error(f"[DeepSeek] Erreur: {e}")
        return None


async def _call_groq(prompt: str) -> Optional[str]:
    """Fallback via Groq."""
    if not settings.groq_api_key:
        return None

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
                    "messages": [
                        {"role": "system", "content": "Tu es un expert en extraction d'informations de documents de marches publics."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1500,
                    "temperature": 0.1,
                },
            )
            data = response.json()

        if response.status_code == 200:
            return data["choices"][0]["message"]["content"].strip()
        return None

    except Exception as e:
        logger.error(f"[Groq] Erreur extraction: {e}")
        return None


async def _call_gemini(prompt: str) -> Optional[str]:
    """Fallback via Gemini."""
    if not settings.gemini_api_key:
        return None

    try:
        import asyncio
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_api_key)

        def _sync():
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
                config=types.GenerateContentConfig(
                    system_instruction="Tu es un expert en extraction d'informations de documents de marches publics.",
                    max_output_tokens=1500,
                    temperature=0.1,
                ),
            )
            return response.text.strip()

        return await asyncio.to_thread(_sync)

    except Exception as e:
        logger.error(f"[Gemini] Erreur extraction: {e}")
        return None


def _parse_extraction(raw_text: str) -> dict:
    """Parse la reponse IA structuree en dict."""
    result = {}
    field_map = {
        "autorite_contractante": "authority_name",
        "objet": "title",
        "reference": "reference",
        "type_marche": "document_type",
        "budget": "budget",
        "garantie_soumission": "guarantee_amount",
        "date_depot": "deadline_date",
        "heure_depot": "deadline_time",
        "lieu_depot": "submission_location",
        "lieu_ouverture": "opening_location",
        "source_financement": "financing_source",
        "lots": "lots_count",
        "conditions_participation": "participation_conditions",
        "documents_requis": "required_documents",
        "contact_email": "authority_email",
        "resume": "summary",
    }

    for line in raw_text.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace(" ", "_")
        value = value.strip()

        if value in ("N/A", "n/a", "Non disponible", "-", ""):
            continue

        mapped_key = field_map.get(key)
        if mapped_key:
            result[mapped_key] = value

    # Convertir budget en float
    if "budget" in result:
        try:
            amount = result["budget"].replace(" ", "").replace(".", "").replace(",", "").replace("FCFA", "").replace("fcfa", "").strip()
            result["budget"] = float(amount)
        except (ValueError, AttributeError):
            del result["budget"]

    if "guarantee_amount" in result:
        try:
            amount = result["guarantee_amount"].replace(" ", "").replace(".", "").replace(",", "").replace("FCFA", "").replace("fcfa", "").strip()
            result["guarantee_amount"] = float(amount)
        except (ValueError, AttributeError):
            del result["guarantee_amount"]

    return result
