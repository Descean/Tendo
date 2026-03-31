"""Service d'analyse IA de documents d'appels d'offres -- Tendo.

Supporte :
- Extraction de texte PDF natif (pypdf)
- OCR pour PDF scannes (pytesseract + pdf2image)
- Fallback Gemini Vision pour les documents complexes
- Classification automatique du type de document
- Analyse IA structuree pour WhatsApp
- Telechargement de medias WhatsApp (images, documents)
"""

import asyncio
import io
import base64
import tempfile
import os
from typing import Optional, Tuple

import httpx

from app.config import settings
from app.utils.logger import logger


# ── Extraction de texte PDF ─────────────────────────────────────────


async def extract_pdf_text(
    pdf_url: str = "",
    pdf_bytes: bytes = b"",
    max_pages: int = 20,
) -> Tuple[Optional[str], str, int]:
    """Extrait le texte d'un PDF par tous les moyens disponibles.

    Strategie :
    1. pypdf (texte natif) — rapide, gratuit
    2. OCR Tesseract (si pypdf echoue) — pour les PDF scannes
    3. Gemini Vision (si OCR echoue) — pour les cas complexes

    Args:
        pdf_url: URL du PDF a telecharger
        pdf_bytes: Contenu brut du PDF (si deja telecharge)
        max_pages: Nombre max de pages

    Returns:
        (texte, methode, nb_pages) ou (None, "failed", 0)
    """
    # 1. Telecharger si necessaire
    if not pdf_bytes and pdf_url:
        pdf_bytes = await _download_file(pdf_url)
        if not pdf_bytes:
            return None, "download_failed", 0

    if not pdf_bytes:
        return None, "no_content", 0

    # 2. Essayer pypdf d'abord (texte natif)
    text, page_count = await _extract_with_pypdf(pdf_bytes, max_pages)
    if text and len(text.strip()) > 100:
        logger.info(f"[DocAnalyzer] pypdf OK: {page_count} pages, {len(text)} chars")
        return text, "pypdf", page_count

    # 3. Essayer OCR Tesseract
    text_ocr, page_count_ocr = await _extract_with_ocr(pdf_bytes, max_pages)
    if text_ocr and len(text_ocr.strip()) > 100:
        logger.info(f"[DocAnalyzer] OCR OK: {page_count_ocr} pages, {len(text_ocr)} chars")
        return text_ocr, "ocr_tesseract", page_count_ocr

    # 4. Fallback Gemini Vision
    text_vision = await _extract_with_gemini_vision(pdf_bytes, max_pages)
    if text_vision and len(text_vision.strip()) > 50:
        logger.info(f"[DocAnalyzer] Gemini Vision OK: {len(text_vision)} chars")
        return text_vision, "gemini_vision", page_count or page_count_ocr or 1

    # 5. Retourner le texte partiel de pypdf si on a au moins quelque chose
    if text and len(text.strip()) > 10:
        return text, "pypdf_partial", page_count

    logger.warning("[DocAnalyzer] Aucune methode n'a pu extraire du texte")
    return None, "failed", 0


async def extract_image_text(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[str]:
    """Extrait le texte d'une image (photo d'un document).

    Utilise Gemini Vision (gratuit) ou Tesseract OCR.
    """
    # Essayer Gemini Vision d'abord
    text = await _extract_image_with_gemini(image_bytes, mime_type)
    if text:
        return text

    # Fallback Tesseract
    text = await _extract_image_with_tesseract(image_bytes)
    return text


async def download_whatsapp_media(media_id: str) -> Optional[bytes]:
    """Telecharge un fichier media depuis WhatsApp (Meta Cloud API).

    Meta envoie un media_id dans le webhook. On doit :
    1. GET /v21.0/{media_id} pour obtenir l'URL temporaire
    2. GET sur l'URL pour telecharger le contenu
    """
    if not settings.meta_access_token:
        logger.error("[DocAnalyzer] META_ACCESS_TOKEN manquant")
        return None

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Etape 1 : obtenir l'URL du media
            headers = {"Authorization": f"Bearer {settings.meta_access_token}"}
            resp = await client.get(
                f"https://graph.facebook.com/v21.0/{media_id}",
                headers=headers,
            )
            if resp.status_code != 200:
                logger.error(f"[DocAnalyzer] Erreur meta media info: {resp.status_code}")
                return None

            media_url = resp.json().get("url")
            if not media_url:
                logger.error("[DocAnalyzer] URL media absente dans la reponse")
                return None

            # Etape 2 : telecharger le contenu
            resp2 = await client.get(media_url, headers=headers)
            if resp2.status_code != 200:
                logger.error(f"[DocAnalyzer] Erreur download media: {resp2.status_code}")
                return None

            logger.info(f"[DocAnalyzer] Media {media_id} telecharge: {len(resp2.content)} octets")
            return resp2.content

    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur download WhatsApp media: {e}")
        return None


# ── Analyse IA ──────────────────────────────────────────────────────


async def analyze_document_full(
    text: str,
    classification: dict,
    user_sectors: list = None,
    user_regions: list = None,
    user_question: str = "",
) -> str:
    """Analyse complete d'un document avec contexte utilisateur.

    Genere une analyse structuree adaptee au profil du client.
    """
    from app.services.claude import chat, _format_for_whatsapp

    doc_type = classification.get("document_type", "AUTRE")
    doc_label = classification.get("label", "Document")

    # Adapter le prompt selon le type de document
    if doc_type in ("AAO", "DAO", "RFP", "AMI", "RFQ"):
        prompt = _build_ao_analysis_prompt(text, classification, user_sectors, user_question)
    elif doc_type in ("PV_ATTRIBUTION", "PV_OUVERTURE", "RAPPORT_EVALUATION"):
        prompt = _build_pv_analysis_prompt(text, classification, user_question)
    elif doc_type == "DECISION_ARMP":
        prompt = _build_armp_analysis_prompt(text, classification, user_question)
    elif doc_type == "PPM":
        prompt = _build_ppm_analysis_prompt(text, classification, user_sectors, user_question)
    else:
        prompt = _build_generic_analysis_prompt(text, classification, user_question)

    result = await chat(prompt, is_premium=True)
    return result


async def analyze_publication(
    title: str,
    summary: str = "",
    html_content: str = "",
    pdf_text: str = "",
    user_question: str = "",
) -> str:
    """Analyse une publication avec l'IA (compatibilite avec l'ancien code)."""
    from app.services.claude import chat

    context_parts = [f"Titre : {title}"]
    if summary:
        context_parts.append(f"Resume : {summary}")
    if html_content:
        context_parts.append(f"Contenu :\n{html_content[:5000]}")
    if pdf_text:
        context_parts.append(f"Contenu du document PDF :\n{pdf_text[:8000]}")

    context = "\n\n".join(context_parts)

    if user_question:
        prompt = (
            f"L'utilisateur pose une question sur cet appel d'offres :\n"
            f"Question : {user_question}\n\n"
            f"Document :\n{context}\n\n"
            f"Reponds de maniere precise et utile a la question."
        )
    else:
        prompt = (
            f"Analyse cet appel d'offres en detail :\n\n{context}\n\n"
            f"Fournis une analyse structuree incluant :\n"
            f"- Objet du marche\n"
            f"- Secteur(s) concerne(s)\n"
            f"- Budget estime (si mentionne)\n"
            f"- Date limite de soumission\n"
            f"- Criteres d'eligibilite principaux\n"
            f"- Documents requis\n"
            f"- Points d'attention\n"
            f"- Recommandation\n\n"
            f"Si certaines informations ne sont pas disponibles, indique-le."
        )

    result = await chat(prompt, is_premium=True, publication_context=context)
    return result


async def build_publication_context(publication) -> str:
    """Construit le contexte complet d'une publication pour l'IA."""
    parts = [
        f"Titre : {publication.title}",
        f"Source : {publication.source}",
        f"Reference : {publication.reference}",
    ]

    if publication.summary:
        parts.append(f"Resume : {publication.summary}")
    if publication.budget:
        parts.append(f"Budget : {publication.budget:,.0f} FCFA")
    if publication.deadline:
        parts.append(f"Date limite : {publication.deadline.strftime('%d/%m/%Y')}")
    if publication.authority_name:
        parts.append(f"Autorite contractante : {publication.authority_name}")
    if publication.html_content:
        parts.append(f"Contenu :\n{publication.html_content[:5000]}")

    if publication.pdf_url:
        pdf_text, method, _ = await extract_pdf_text(pdf_url=publication.pdf_url)
        if pdf_text:
            parts.append(f"Document PDF ({method}) :\n{pdf_text[:8000]}")

    return "\n\n".join(parts)


def check_relevance_for_user(
    classification: dict,
    user_sectors: list,
    user_regions: list,
) -> dict:
    """Verifie si un document est pertinent pour un utilisateur donne.

    Returns:
        {
            "is_relevant": True/False,
            "relevance_score": 0.0 à 1.0,
            "matching_sectors": [...],
            "matching_regions": [...],
            "reason": "Correspond a vos secteurs BTP et region Cotonou",
        }
    """
    doc_sectors = classification.get("sectors", [])
    doc_regions = classification.get("regions", [])
    doc_type = classification.get("document_type", "")

    matching_sectors = [s for s in doc_sectors if s in (user_sectors or [])]
    matching_regions = [r for r in doc_regions if r in (user_regions or [])]

    # Score de pertinence
    score = 0.0
    reasons = []

    if matching_sectors:
        score += 0.5
        reasons.append(f"Secteur{'s' if len(matching_sectors)>1 else ''} : {', '.join(matching_sectors)}")

    if matching_regions:
        score += 0.3
        reasons.append(f"Region{'s' if len(matching_regions)>1 else ''} : {', '.join(matching_regions)}")

    # Les PV d'attribution et decisions ARMP sont toujours pertinents
    # si le secteur matche (le client veut savoir qui a gagne)
    if doc_type in ("PV_ATTRIBUTION", "DECISION_ARMP", "RAPPORT_EVALUATION"):
        if matching_sectors:
            score += 0.2
            reasons.append("Info complementaire sur vos marches")

    # Si pas de filtre defini, tout est pertinent
    if not user_sectors and not user_regions:
        score = 0.5
        reasons.append("Aucun filtre de preference defini")

    return {
        "is_relevant": score >= 0.3,
        "relevance_score": min(score, 1.0),
        "matching_sectors": matching_sectors,
        "matching_regions": matching_regions,
        "reason": " | ".join(reasons) if reasons else "Non pertinent pour votre profil",
    }


# ── Methodes d'extraction privees ──────────────────────────────────


async def _download_file(url: str, retries: int = 3) -> Optional[bytes]:
    """Telecharge un fichier depuis une URL avec retry et fallback IPv4."""
    for attempt in range(retries):
        try:
            transport = httpx.AsyncHTTPTransport(
                retries=2,
                local_address="0.0.0.0",  # Force IPv4
            )
            async with httpx.AsyncClient(
                timeout=90,
                follow_redirects=True,
                transport=transport,
            ) as client:
                response = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/120.0"
                })
                if response.status_code == 200:
                    return response.content
                logger.error(f"[DocAnalyzer] Download echoue: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"[DocAnalyzer] Download tentative {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                import asyncio
                await asyncio.sleep(3)
    logger.error(f"[DocAnalyzer] Echec download apres {retries} tentatives: {url}")
    return None


async def _extract_with_pypdf(pdf_bytes: bytes, max_pages: int) -> Tuple[Optional[str], int]:
    """Extraction de texte avec pypdf (PDF natif)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        text_parts = []

        for i, page in enumerate(reader.pages[:max_pages]):
            page_text = page.extract_text()
            if page_text and page_text.strip():
                text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        if text_parts:
            return "\n\n".join(text_parts), page_count
        return None, page_count

    except ImportError:
        logger.warning("[DocAnalyzer] pypdf non installe")
        return None, 0
    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur pypdf: {e}")
        return None, 0


async def _extract_with_ocr(pdf_bytes: bytes, max_pages: int) -> Tuple[Optional[str], int]:
    """Extraction de texte par OCR (Tesseract + pdf2image)."""
    try:
        from pdf2image import convert_from_bytes
        import pytesseract
    except ImportError:
        logger.warning("[DocAnalyzer] pdf2image ou pytesseract non installe — OCR indisponible")
        return None, 0

    try:
        # Convertir PDF en images (en thread pour ne pas bloquer l'event loop)
        def _do_ocr():
            images = convert_from_bytes(
                pdf_bytes,
                first_page=1,
                last_page=min(max_pages, 20),
                dpi=200,
                fmt="jpeg",
            )
            text_parts = []
            for i, img in enumerate(images):
                # OCR avec Tesseract (francais + anglais)
                page_text = pytesseract.image_to_string(img, lang="fra+eng")
                if page_text and page_text.strip():
                    text_parts.append(f"--- Page {i + 1} (OCR) ---\n{page_text}")
            return "\n\n".join(text_parts) if text_parts else None, len(images)

        text, page_count = await asyncio.to_thread(_do_ocr)
        return text, page_count

    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur OCR: {e}")
        return None, 0


async def _extract_with_gemini_vision(pdf_bytes: bytes, max_pages: int = 5) -> Optional[str]:
    """Extraction de texte avec Gemini Vision (gratuit, 15 req/min)."""
    if not settings.gemini_api_key:
        return None

    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)

        # Convertir les premieres pages en images
        try:
            from pdf2image import convert_from_bytes
            images = await asyncio.to_thread(
                convert_from_bytes,
                pdf_bytes,
                first_page=1,
                last_page=min(max_pages, 5),
                dpi=150,
                fmt="jpeg",
            )
        except ImportError:
            # Si pdf2image n'est pas dispo, envoyer le PDF directement
            logger.warning("[DocAnalyzer] pdf2image non dispo pour Gemini Vision")
            return None

        text_parts = []
        for i, img in enumerate(images[:5]):  # Max 5 pages pour Gemini gratuit
            # Convertir en bytes
            img_buffer = io.BytesIO()
            img.save(img_buffer, format="JPEG", quality=85)
            img_bytes = img_buffer.getvalue()
            img_b64 = base64.b64encode(img_bytes).decode()

            def _call_gemini(b64_data, page_num):
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[
                        {
                            "parts": [
                                {"text": f"Extrais tout le texte de cette page {page_num} d'un document administratif. Retourne uniquement le texte brut, sans commentaire."},
                                {"inline_data": {"mime_type": "image/jpeg", "data": b64_data}},
                            ]
                        }
                    ],
                )
                return response.text if response.text else ""

            page_text = await asyncio.to_thread(_call_gemini, img_b64, i + 1)
            if page_text.strip():
                text_parts.append(f"--- Page {i + 1} (Vision) ---\n{page_text}")

            await asyncio.sleep(1)  # Rate limit Gemini gratuit

        return "\n\n".join(text_parts) if text_parts else None

    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur Gemini Vision: {e}")
        return None


async def _extract_image_with_gemini(image_bytes: bytes, mime_type: str) -> Optional[str]:
    """Extrait le texte d'une image avec Gemini Vision."""
    if not settings.gemini_api_key:
        return None

    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        img_b64 = base64.b64encode(image_bytes).decode()

        def _call():
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[{
                    "parts": [
                        {"text": "Extrais tout le texte de cette image d'un document. Retourne uniquement le texte brut."},
                        {"inline_data": {"mime_type": mime_type, "data": img_b64}},
                    ]
                }],
            )
            return response.text if response.text else ""

        return await asyncio.to_thread(_call)

    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur Gemini Vision image: {e}")
        return None


async def _extract_image_with_tesseract(image_bytes: bytes) -> Optional[str]:
    """Extrait le texte d'une image avec Tesseract OCR."""
    try:
        import pytesseract
        from PIL import Image

        def _do_ocr():
            img = Image.open(io.BytesIO(image_bytes))
            return pytesseract.image_to_string(img, lang="fra+eng")

        text = await asyncio.to_thread(_do_ocr)
        return text if text and text.strip() else None

    except ImportError:
        logger.warning("[DocAnalyzer] pytesseract non installe")
        return None
    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur Tesseract image: {e}")
        return None


# ── Prompts d'analyse par type ─────────────────────────────────────


def _build_ao_analysis_prompt(text: str, classification: dict, user_sectors: list, question: str) -> str:
    """Prompt pour analyser un appel d'offres / DAO."""
    doc_label = classification.get("label", "Appel d'offres")
    sectors_ctx = f"\nSecteurs de l'utilisateur : {', '.join(user_sectors)}" if user_sectors else ""

    if question:
        return (
            f"Tu es Tendo, expert des marches publics au Benin.\n"
            f"L'utilisateur pose une question sur ce {doc_label} :\n"
            f"Question : {question}\n\n"
            f"Document :\n{text[:8000]}\n{sectors_ctx}\n\n"
            f"Reponds de maniere precise et utile."
        )

    return (
        f"Tu es Tendo, expert des marches publics au Benin et Afrique de l'Ouest.\n"
        f"Analyse ce {doc_label} en detail :\n\n{text[:8000]}\n{sectors_ctx}\n\n"
        f"Fournis une analyse structuree :\n\n"
        f"*OBJET DU MARCHE*\n[Objet]\n\n"
        f"*TYPE DE MARCHE*\n[Travaux / Fournitures / Services]\n\n"
        f"*BUDGET ESTIME*\n[Montant si disponible]\n\n"
        f"*DATE LIMITE*\n[Date de soumission]\n\n"
        f"*AUTORITE CONTRACTANTE*\n[Nom]\n\n"
        f"*PIECES REQUISES*\n"
        f"Administratives : [liste]\n"
        f"Techniques : [liste]\n"
        f"Financieres : [liste]\n\n"
        f"*CRITERES D'EVALUATION*\n[criteres et ponderation]\n\n"
        f"*POINTS D'ATTENTION*\n[clauses importantes, risques]\n\n"
        f"*RECOMMANDATION*\n[pertinence pour un soumissionnaire]\n\n"
        f"Si certaines informations ne sont pas disponibles, indique-le."
    )


def _build_pv_analysis_prompt(text: str, classification: dict, question: str) -> str:
    """Prompt pour analyser un PV d'attribution / ouverture."""
    doc_label = classification.get("label", "Proces-verbal")

    if question:
        return (
            f"Tu es Tendo, expert des marches publics.\n"
            f"Question sur ce {doc_label} : {question}\n\n"
            f"Document :\n{text[:8000]}\n\n"
            f"Reponds de maniere precise."
        )

    return (
        f"Tu es Tendo, expert des marches publics au Benin.\n"
        f"Analyse ce {doc_label} :\n\n{text[:8000]}\n\n"
        f"Fournis :\n\n"
        f"*REFERENCE DU MARCHE*\n[Reference de l'AO]\n\n"
        f"*TITULAIRE / ATTRIBUTAIRE*\n[Nom de l'entreprise gagnante]\n\n"
        f"*MONTANT ATTRIBUE*\n[Montant]\n\n"
        f"*NOMBRE D'OFFRES*\n[Nombre d'offres recues]\n\n"
        f"*CLASSEMENT*\n[Top 3 si disponible]\n\n"
        f"*OBSERVATIONS*\n[Points importants, lecons a tirer]"
    )


def _build_armp_analysis_prompt(text: str, classification: dict, question: str) -> str:
    """Prompt pour analyser une decision ARMP."""
    subtype = classification.get("document_subtype", "")

    if question:
        return (
            f"Tu es Tendo, expert en reglementation des marches publics au Benin.\n"
            f"Question sur cette decision ARMP : {question}\n\n"
            f"Document :\n{text[:8000]}\n\n"
            f"Reponds de maniere precise."
        )

    return (
        f"Tu es Tendo, expert en reglementation des marches publics au Benin.\n"
        f"Analyse cette decision de l'ARMP :\n\n{text[:8000]}\n\n"
        f"Fournis :\n\n"
        f"*NUMERO DE DECISION*\n[N°]\n\n"
        f"*TYPE*\n[Recours / Sanction / Autosaisine / Autre]\n\n"
        f"*ENTREPRISES CONCERNEES*\n[Noms]\n\n"
        f"*AUTORITE CONTRACTANTE*\n[Nom]\n\n"
        f"*MOTIFS*\n[Resume des motifs]\n\n"
        f"*DECISION*\n[Ce qui a ete decide]\n\n"
        f"*CONSEQUENCES*\n[Exclusion? Duree? Reprise de procedure?]\n\n"
        f"*LECONS A TIRER*\n[Ce que les soumissionnaires doivent retenir]"
    )


def _build_ppm_analysis_prompt(text: str, classification: dict, user_sectors: list, question: str) -> str:
    """Prompt pour analyser un Plan de Passation des Marches."""
    sectors_ctx = f"\nSecteurs de l'utilisateur : {', '.join(user_sectors)}" if user_sectors else ""

    return (
        f"Tu es Tendo, expert des marches publics au Benin.\n"
        f"Analyse ce Plan de Passation des Marches :\n\n{text[:8000]}\n{sectors_ctx}\n\n"
        f"Fournis :\n\n"
        f"*AUTORITE / PROJET*\n[Nom]\n\n"
        f"*ANNEE / PERIODE*\n[Annee couverte]\n\n"
        f"*MARCHES PREVUS*\n"
        f"Pour chaque marche pertinent, indique :\n"
        f"- Objet\n- Type (travaux/fournitures/services)\n"
        f"- Trimestre de lancement prevu\n- Budget indicatif\n"
        f"- Mode de passation\n\n"
        f"*OPPORTUNITES POUR L'UTILISATEUR*\n"
        f"[Quels marches correspondent a son profil]"
    )


def _build_generic_analysis_prompt(text: str, classification: dict, question: str) -> str:
    """Prompt generique pour les documents non classifies."""
    if question:
        return (
            f"Tu es Tendo, expert des marches publics.\n"
            f"Question : {question}\n\n"
            f"Document :\n{text[:8000]}\n\n"
            f"Reponds de maniere precise et utile."
        )

    return (
        f"Tu es Tendo, expert des marches publics au Benin et Afrique de l'Ouest.\n"
        f"Analyse ce document :\n\n{text[:8000]}\n\n"
        f"Identifie :\n"
        f"- Le type de document\n"
        f"- L'objet principal\n"
        f"- Les informations cles\n"
        f"- Les points d'attention pour un soumissionnaire\n\n"
        f"Presente de maniere structuree."
    )
