"""Service de lecture intelligente de documents via DeepSeek.

Pipeline d'extraction en 2 phases :
  Phase 1 — Regex : extraction rapide des champs standard (gratuit, instantane)
  Phase 2 — DeepSeek : completion des champs manquants + resume IA

Fallback IA: DeepSeek > Groq > Gemini si DeepSeek indisponible.
"""

import re
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings
from app.utils.logger import logger


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1 : CLASSIFICATION DU TYPE DE DOCUMENT (regex)
# ═══════════════════════════════════════════════════════════════════

# Ordre de priorite : les patterns les plus specifiques en premier
DOCUMENT_TYPE_PATTERNS = [
    # Decisions / PV (avant les appels d'offres car le texte peut contenir des termes d'AO)
    ("DECISION_ARMP", [
        r"d[eé]cision\s+(?:de\s+l.)?armp",
        r"d[eé]cision\s+n[°o]",
        r"ARMP\s*[\-\u2013\u2014]\s*Avis\s+N\s*\d+",  # "ARMP – Avis N2026 025"
        r"ARMP\s*[\-\u2013\u2014]\s*D[eé]cision",
        r"conseil\s+de\s+r[eé]gulation",
    ]),
    # Demandes de prix / devis
    ("DRP", [
        r"demande\s+de\s+renseignements\s+et\s+de\s+prix",
        r"demande\s+de\s+prix",
        r"consultation\s+restreinte",
    ]),
    ("RFQ", [
        r"demande\s+de\s+(?:devis|cotation)",
        r"request\s+for\s+quotation",
    ]),
    ("RFP", [
        r"demande\s+de\s+propositions",
        r"request\s+for\s+proposal",
    ]),
    # Appels d'offres
    ("AOI", [
        r"appel\s+d.offres?\s+international",
        r"international\s+competitive\s+bidding",
    ]),
    ("AOR", [
        r"appel\s+d.offres?\s+restreint",
    ]),
    ("AON", [
        r"appel\s+d.offres?\s+(?:national|ouvert\s+national)",
    ]),
    ("AMI", [
        r"appel\s+[aà]\s+manifestation\s+d.int[eé]r[eê]t",
        r"manifestation\s+d.int[eé]r[eê]t",
        r"liste\s+restreinte",
        r"shortlist",
    ]),
    ("AAO", [
        r"avis\s+d.appel\s+[aà]\s+(?:la\s+)?concurrence",
        r"avis\s+d.appel\s+d.offres?",
        r"dossier\s+d.appel\s+d.offres?",
        r"appel\s+d.offres?",
    ]),
    # Resultats / PV
    ("PV_OUVERTURE", [
        r"proc[eè]s[\s-]verbal\s+d.ouverture",
        r"pv\s+d.ouverture",
    ]),
    ("PV_ATTRIBUTION", [
        r"proc[eè]s[\s-]verbal\s+d.attribution",
        r"pv\s+d.attribution",
        r"attribution\s+provisoire",
    ]),
    ("AVIS_ATTRIBUTION", [
        r"avis\s+d.attribution\s+d[eé]finitive",
        r"attribution\s+d[eé]finitive",
    ]),
    # Autres
    ("ADDITIF", [
        r"addendum", r"additif", r"corrigendum", r"avenant",
        r"report\s+de\s+date", r"prolongation",
    ]),
    ("PPM", [
        r"plan\s+(?:de\s+)?passation",
        r"plan\s+pr[eé]visionnel",
    ]),
]

DOCUMENT_TYPE_LABELS = {
    "DRP": "Demande de renseignements et prix (DRP)",
    "RFQ": "Demande de devis (RFQ)",
    "RFP": "Demande de propositions (RFP)",
    "AOI": "Appel d'offres international (AOI)",
    "AOR": "Appel d'offres restreint (AOR)",
    "AON": "Appel d'offres national (AON)",
    "AMI": "Appel a manifestation d'interet (AMI)",
    "AAO": "Avis d'appel a concurrence",
    "PV_OUVERTURE": "PV d'ouverture des plis",
    "PV_ATTRIBUTION": "PV d'attribution provisoire",
    "AVIS_ATTRIBUTION": "Avis d'attribution definitive",
    "DECISION_ARMP": "Decision ARMP",
    "ADDITIF": "Addendum / Additif",
    "PPM": "Plan de passation des marches",
}


def classify_document(text: str) -> tuple[str, str]:
    """Classifie le type de document par analyse lexicale.

    Returns:
        (code, label) ex: ("DRP", "Demande de renseignements et prix (DRP)")
    """
    text_lower = text[:5000].lower()
    for code, patterns in DOCUMENT_TYPE_PATTERNS:
        for pat in patterns:
            if re.search(pat, text_lower):
                label = DOCUMENT_TYPE_LABELS.get(code, code)
                logger.info(f"[Classifier] Type detecte: {code} ({label})")
                return code, label

    return "AAO", "Avis d'appel a concurrence"


# ═══════════════════════════════════════════════════════════════════
#  PHASE 1 : EXTRACTION REGEX (gratuite, instantanee)
# ═══════════════════════════════════════════════════════════════════

def extract_with_regex(text: str) -> dict:
    """Extrait les metadonnees par expressions regulieres.

    Rapide, gratuit, fiable sur les formats standards beninois.
    """
    result = {}
    if not text:
        return result

    # ── Autorite contractante ──
    for pat in [
        r"(?:autorit[eé]\s*contractante|ma[iî]tre\s*d['']\s*ouvrage)\s*[:]\s*(.{5,150}?)(?:\n|T[eé]l|Email|Adresse|BP\s|Fax)",
        r"(?:Mairie|Commune|Minist[eè]re|Direction\s+G[eé]n[eé]rale|Agence|Office|Soci[eé]t[eé]|PRMP|Centre|H[oô]pital)\s+(?:de\s+|du\s+|des\s+|d['']\s*)?[A-Z\u00C0-\u00FF][a-zA-Z\u00E0-\u00FF\s\-/]{3,80}",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            name = m.group(1) if m.lastindex else m.group(0)
            name = re.sub(r"\s+", " ", name).strip().rstrip(".,;:")
            if len(name) > 5 and "@" not in name:
                result["authority_name"] = name
                break

    # ── Objet du marche ──
    for pat in [
        r"(?:objet|intitul[eé])\s*(?:du\s*march[eé]|de\s*la\s*(?:DRP|consultation))?\s*[:]\s*(.{20,500}?)(?:\n\n|\nR[eé]f|\nDate|\nLieu|\nSource|\nArticle)",
        r"(?:objet|intitul[eé])\s*[:]\s*(.{20,400})",
    ]:
        m = re.search(pat, text, re.I | re.DOTALL)
        if m:
            obj = re.sub(r"\s+", " ", m.group(1)).strip().rstrip(".,;:")
            result["title"] = obj
            break

    # ── Reference SIGMAP ──
    for pat in [
        r"(?:R[eé]f[eé]rence\s*(?:SIGMAP|de\s*la\s*DRP)?)\s*[:]\s*([A-Z0-9][\w\s_\-/]{3,40})",
        r"(?:N[°o]\s*(?:du\s*)?(?:march[eé]|dossier|DAO|DRP))\s*[:]\s*([A-Z0-9][\w\s_\-/]{3,40})",
        r"\b((?:F|T|S|PI)[\s_][A-Z]{2,8}[\s_]\d{4,8})\b",  # Format SIGMAP: F_SAAE_116151
    ]:
        m = re.search(pat, text, re.I)
        if m:
            ref = re.sub(r"\s+", " ", m.group(1)).strip()
            # Nettoyer les espaces parasites dans les refs SIGMAP
            ref = re.sub(r"([A-Z])[\s_]+([A-Z])", r"\1_\2", ref)
            ref = re.sub(r"([A-Z])[\s_]+(\d)", r"\1_\2", ref)
            result["reference"] = ref
            break

    # ── Date limite de depot ──
    for pat in [
        r"au\s+plus\s+tard\s+le\s*(\d{1,2}[\s./\-]+\d{1,2}[\s./\-]+\d{4})",
        r"date\s*(?:et\s*heure\s*)?limite\s*(?:de\s*(?:d[eé]p[oô]t|r[eé]ception|soumission))?\s*[:]\s*(\d{1,2}[\s./\-]+\d{1,2}[\s./\-]+\d{4})",
        r"avant\s+le\s*(\d{1,2}[\s./\-]+\d{1,2}[\s./\-]+\d{4})",
        r"(\d{1,2}[\s./\-]+\d{1,2}[\s./\-]+\d{4})\s*[àa]\s*\d{1,2}\s*[hH]",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            raw = m.group(1).strip()
            parsed = _parse_date_flexible(raw)
            if parsed:
                result["deadline_date"] = parsed
                break

    # ── Heure limite ──
    for pat in [
        r"(?:au\s+plus\s+tard|limite|avant)\s+.*?[àa]\s*(\d{1,2})\s*(?:[hH:])\s*(\d{0,2})",
        r"(\d{1,2})\s*[hH]\s*(\d{0,2})\s*(?:mn|min|minutes?)?\s*(?:locale|pr[eé]cise|TU|GMT)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            h = m.group(1)
            mins = m.group(2) or "00"
            result["deadline_time"] = f"{h}h{mins.zfill(2)}"
            break

    # ── Email de contact ──
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
    if m:
        result["authority_email"] = m.group(0).lower()

    # ── Budget / Montant ──
    for pat in [
        r"(?:budget|montant\s*(?:estim[eé]|pr[eé]visionnel)?)\s*[:]\s*([0-9\s.,]+)\s*(?:F\s*CFA|FCFA|XOF)",
        r"([0-9\s.,]{6,})\s*(?:F\s*CFA|FCFA|XOF)\b",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            amount_str = m.group(1).replace(" ", "").replace(".", "").replace(",", "")
            try:
                result["budget"] = float(amount_str)
            except ValueError:
                pass
            break

    # ── Garantie de soumission ──
    for pat in [
        r"(?:garantie\s*(?:de\s*)?soumission|caution\s*provisoire)\s*[:]\s*([0-9\s.,]+)\s*(?:F\s*CFA|FCFA|XOF)?",
        r"garantie\s*(?:de\s*)?soumission\s+.*?([0-9\s.,]{6,})\s*(?:F\s*CFA|FCFA|XOF)?",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            amount_str = m.group(1).replace(" ", "").replace(".", "").replace(",", "")
            try:
                result["guarantee_amount"] = float(amount_str)
            except ValueError:
                pass
            break

    # ── Source de financement ──
    for pat in [
        r"(?:source\s*(?:de\s*)?financement|financ[eé]\s*(?:par|sur))\s*[:]\s*(.{5,100}?)(?:\n|\.)",
        r"(?:budget\s*(?:national|g[eé]n[eé]ral|d['']\s*[eé]tat)|BN|BAD|BM|Banque\s+[Mm]ondiale|UE|AFD|FED|BOAD|FMI|PNUD)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            src = m.group(1) if m.lastindex else m.group(0)
            src = re.sub(r"\s+", " ", src).strip()
            # Normaliser
            src_lower = src.lower()
            if "budget national" in src_lower or "budget g" in src_lower or src_lower == "bn":
                result["financing_source"] = "BN"
            elif "bad" in src_lower or "banque africaine" in src_lower:
                result["financing_source"] = "BAD"
            elif "banque mondiale" in src_lower or "bm" == src_lower or "ida" in src_lower:
                result["financing_source"] = "BM"
            elif "afd" in src_lower:
                result["financing_source"] = "AFD"
            elif any(x in src_lower for x in ("ue", "union europ", "fed")):
                result["financing_source"] = "UE"
            else:
                result["financing_source"] = src
            break

    # ── Lots (detail) ──
    lots_info = _extract_lots(text)
    if lots_info:
        result["lots_detail"] = lots_info["detail"]
        result["lots_count"] = lots_info["count"]

    # ── Delai de livraison / execution ──
    for pat in [
        r"d[eé]lai\s+d.(?:ex[eé]cution|livraison)\s+(?:est\s+)?(?:de\s+)?(\w+)\s*(?:\(\d+\)\s*)?jours",
        r"d[eé]lai\s+d.(?:ex[eé]cution|livraison)\s*[:]\s*(\d+)\s*jours",
        r"d[eé]lai\s+(?:est\s+)?(?:de\s+)?(\w+)\s*(?:\(\d+\)\s*)?jours\s*(?:calendaires|ouvr[eé]s|ouvrables)?",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            val = m.group(1).strip()
            # Convertir les mots en chiffres
            days = _word_to_number(val)
            if days:
                result["delivery_delay"] = f"{days} jours"
            break

    # ── Lieu de depot ──
    for pat in [
        r"(?:lieu\s*(?:de\s*)?d[eé]p[oô]t|d[eé]pos[eé]s?\s*(?:au|[aà]))\s*[:]\s*(.{10,200}?)(?:\n\n|\nDate|\nLieu|\nHeure)",
        r"secr[eé]tariat\s+(?:permanent\s+)?(?:de\s+la\s+)?(?:PRMP|personne\s+responsable)[^.]{10,200}",
    ]:
        m = re.search(pat, text, re.I | re.DOTALL)
        if m:
            lieu = m.group(1) if m.lastindex else m.group(0)
            lieu = re.sub(r"\s+", " ", lieu).strip()
            result["submission_location"] = lieu
            break

    return result


def _extract_lots(text: str) -> Optional[dict]:
    """Extrait les details des lots (nombre + description)."""
    # Chercher "en X lots"
    m = re.search(r"(?:en|compos[eé]\s+de|divis[eé]\s+en)\s+(\w+)\s+lots?", text, re.I)
    count = None
    if m:
        count = _word_to_number(m.group(1))

    # Chercher les lots individuels
    lot_pattern = r"Lot\s*(\d+)\s*[:]\s*(.{10,200}?)(?=\nLot\s*\d|\n\n|\Z)"
    lots = re.findall(lot_pattern, text, re.I | re.DOTALL)

    if lots:
        details = []
        for num, desc in lots:
            desc = re.sub(r"\s+", " ", desc).strip().rstrip(".,;:")
            details.append(f"Lot {num} ({desc})")
        return {
            "count": count or len(lots),
            "detail": " / ".join(details),
        }

    if count:
        return {"count": count, "detail": f"{count} lots"}

    return None


def _word_to_number(word: str) -> Optional[int]:
    """Convertit un mot (francais) en nombre."""
    try:
        return int(word)
    except ValueError:
        pass

    words_map = {
        "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
        "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
        "onze": 11, "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15,
        "seize": 16, "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50,
        "soixante": 60, "quatre-vingt": 80, "quatre-vingt-dix": 90, "cent": 100,
    }
    return words_map.get(word.lower().strip())


def _parse_date_flexible(raw: str) -> Optional[str]:
    """Parse une date flexible (avec points, espaces, slashes) en JJ/MM/AAAA."""
    # Nettoyer : "28. 5. 2026" → "28/5/2026"
    cleaned = re.sub(r"[\s.]+", "/", raw.strip())
    cleaned = re.sub(r"/+", "/", cleaned)

    # Essayer differents formats
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"]:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


# ═══════════════════════════════════════════════════════════════════
#  PHASE 2 : EXTRACTION IA (DeepSeek > Groq > Gemini)
# ═══════════════════════════════════════════════════════════════════

EXTRACTION_PROMPT = """Tu es un expert en marches publics au Benin et en Afrique de l'Ouest.
Analyse ce document et extrais les informations suivantes.
Si une information n'est pas disponible, ecris "N/A".

IMPORTANT : Certaines informations ont deja ete extraites par analyse automatique.
Complete UNIQUEMENT les champs marques [A COMPLETER]. Les autres sont deja remplis.

{prefilled}

Reponds UNIQUEMENT avec les champs ci-dessous, un par ligne, au format "Champ: valeur" :

Autorite_contractante: {auth_hint}
Objet: {obj_hint}
Reference: {ref_hint}
Type_marche: {type_hint}
Budget: {budget_hint}
Garantie_soumission: {guarantee_hint}
Date_depot: {deadline_hint}
Heure_depot: {time_hint}
Lieu_depot: {lieu_hint}
Source_financement: {fin_hint}
Lots: {lots_hint}
Delai_livraison: {delai_hint}
Contact_email: {email_hint}
Resume: [resume en 3-4 phrases du contenu du document, en francais]

DOCUMENT :
{content}"""


async def extract_document_info(text_content: str) -> Optional[dict]:
    """Pipeline complet d'extraction : regex d'abord, puis IA pour completer.

    Phase 1 : regex (gratuit, instantane)
    Phase 2 : DeepSeek IA (pour les champs manquants + resume)
    """
    if not text_content or len(text_content.strip()) < 50:
        return None

    # ── Phase 1 : extraction regex ──
    regex_data = extract_with_regex(text_content)
    doc_type, doc_label = classify_document(text_content)

    logger.info(
        f"[Extractor] Phase 1 regex: {len(regex_data)} champs extraits, "
        f"type={doc_type}"
    )

    # ── Phase 2 : IA pour completer ──
    content = text_content[:30000]

    # Construire le prompt avec les hints des champs deja extraits
    def hint(key, label="[A COMPLETER]"):
        val = regex_data.get(key)
        return f"{val} (confirme ou corrige)" if val else label

    prefilled_parts = []
    if regex_data:
        prefilled_parts.append("Champs deja extraits automatiquement :")
        for k, v in regex_data.items():
            prefilled_parts.append(f"  - {k}: {v}")

    prompt = EXTRACTION_PROMPT.format(
        prefilled="\n".join(prefilled_parts),
        content=content,
        auth_hint=hint("authority_name"),
        obj_hint=hint("title"),
        ref_hint=hint("reference"),
        type_hint=f"{doc_label} (confirme ou corrige)" if doc_type else "[A COMPLETER - Travaux / Fournitures / Services / DRP / etc.]",
        budget_hint=hint("budget"),
        guarantee_hint=hint("guarantee_amount"),
        deadline_hint=hint("deadline_date"),
        time_hint=hint("deadline_time"),
        lieu_hint=hint("submission_location"),
        fin_hint=hint("financing_source"),
        lots_hint=hint("lots_detail"),
        delai_hint=hint("delivery_delay"),
        email_hint=hint("authority_email"),
    )

    # Appeler l'IA (cascade)
    ia_result = await _call_deepseek(prompt)
    if not ia_result:
        ia_result = await _call_groq(prompt)
    if not ia_result:
        ia_result = await _call_gemini(prompt)

    if ia_result:
        ia_data = _parse_extraction(ia_result)
    else:
        ia_data = {}

    # ── Fusion : regex a priorite, IA complete ──
    merged = {}

    # D'abord les donnees IA
    for k, v in ia_data.items():
        merged[k] = v

    # Puis les donnees regex (ecrasent l'IA si presentes)
    field_map_regex = {
        "authority_name": "authority_name",
        "title": "title",
        "reference": "reference",
        "deadline_date": "deadline_date",
        "deadline_time": "deadline_time",
        "authority_email": "authority_email",
        "budget": "budget",
        "guarantee_amount": "guarantee_amount",
        "financing_source": "financing_source",
        "submission_location": "submission_location",
        "delivery_delay": "delivery_delay",
        "lots_detail": "lots_detail",
        "lots_count": "lots_count",
    }
    for regex_key, merged_key in field_map_regex.items():
        if regex_key in regex_data and regex_data[regex_key]:
            merged[merged_key] = regex_data[regex_key]

    # Toujours forcer le type de document classifie
    merged["document_type"] = doc_type
    merged["document_type_label"] = doc_label

    logger.info(
        f"[Extractor] Fusion terminee: {len(merged)} champs "
        f"(regex={len(regex_data)}, ia={len(ia_data)})"
    )

    return merged


# ═══════════════════════════════════════════════════════════════════
#  APPELS API (inchanges)
# ═══════════════════════════════════════════════════════════════════

async def _call_deepseek(prompt: str) -> Optional[str]:
    """Appelle l'API DeepSeek (compatible OpenAI)."""
    if not settings.deepseek_api_key:
        return None

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Tu es un expert en extraction d'informations de documents "
                                "de marches publics au Benin et en Afrique de l'Ouest. "
                                "Tu dois extraire les informations avec precision. "
                                "Reponds en francais. Sois concis et factuel."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2500,
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
                    "max_tokens": 2500,
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
                    max_output_tokens=2500,
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
        "lots": "lots_detail",
        "delai_livraison": "delivery_delay",
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
        key = key.strip().lower().replace(" ", "_").replace("'", "")
        value = value.strip()

        if value in ("N/A", "n/a", "Non disponible", "-", "", "[A COMPLETER]"):
            continue

        # Supprimer les hints "(confirme ou corrige)"
        value = re.sub(r"\s*\(confirme ou corrige\)\s*$", "", value).strip()

        mapped_key = field_map.get(key)
        if mapped_key:
            result[mapped_key] = value

    # Convertir budget en float
    for num_field in ("budget", "guarantee_amount"):
        if num_field in result and isinstance(result[num_field], str):
            try:
                amount = (
                    result[num_field]
                    .replace(" ", "").replace(".", "").replace(",", "")
                    .replace("FCFA", "").replace("fcfa", "").replace("XOF", "")
                    .strip()
                )
                result[num_field] = float(amount)
            except (ValueError, AttributeError):
                del result[num_field]

    return result
