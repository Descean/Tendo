"""Service d'analyse IA de documents d'appels d'offres -- Tendo.

Permet aux utilisateurs d'envoyer une reference de publication
et d'obtenir une analyse detaillee par l'IA.
"""

import io
from typing import Optional

import httpx

from app.utils.logger import logger


async def extract_pdf_text(pdf_url: str, max_pages: int = 10) -> Optional[str]:
    """Telecharge un PDF et en extrait le texte.

    Args:
        pdf_url: URL du document PDF
        max_pages: Nombre maximum de pages a extraire

    Returns:
        Le texte extrait ou None si echec
    """
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(pdf_url)
            if response.status_code != 200:
                logger.error(f"[DocAnalyzer] Erreur telechargement PDF: HTTP {response.status_code}")
                return None

            content_type = response.headers.get("content-type", "")
            if "pdf" not in content_type and not pdf_url.lower().endswith(".pdf"):
                logger.warning(f"[DocAnalyzer] Le fichier ne semble pas etre un PDF: {content_type}")

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(response.content))
        text_parts = []

        for i, page in enumerate(reader.pages[:max_pages]):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        if not text_parts:
            logger.warning("[DocAnalyzer] Aucun texte extrait du PDF")
            return None

        full_text = "\n\n".join(text_parts)
        logger.info(f"[DocAnalyzer] PDF extrait: {len(reader.pages)} pages, {len(full_text)} caracteres")
        return full_text

    except ImportError:
        logger.error("[DocAnalyzer] pypdf non installe. Installez: pip install pypdf")
        return None
    except Exception as e:
        logger.error(f"[DocAnalyzer] Erreur extraction PDF: {e}")
        return None


async def analyze_publication(
    title: str,
    summary: str = "",
    html_content: str = "",
    pdf_text: str = "",
    user_question: str = "",
) -> str:
    """Analyse une publication avec l'IA et retourne une synthese detaillee.

    Args:
        title: Titre de la publication
        summary: Resume existant
        html_content: Contenu HTML scrape
        pdf_text: Texte extrait du PDF
        user_question: Question specifique de l'utilisateur (optionnel)

    Returns:
        Analyse detaillee formatee pour WhatsApp
    """
    from app.services.claude import chat, _format_for_whatsapp

    # Construire le contexte complet
    context_parts = [f"Titre : {title}"]
    if summary:
        context_parts.append(f"Resume : {summary}")
    if html_content:
        # Limiter le contenu HTML
        clean_content = html_content[:5000]
        context_parts.append(f"Contenu :\n{clean_content}")
    if pdf_text:
        # Limiter le texte PDF
        clean_pdf = pdf_text[:8000]
        context_parts.append(f"Contenu du document PDF :\n{clean_pdf}")

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
            f"- Recommandation (pertinent ou non pour un soumissionnaire)\n\n"
            f"Si certaines informations ne sont pas disponibles, indique-le."
        )

    # Utiliser le chat avec contexte de publication
    result = await chat(
        prompt,
        is_premium=True,  # Utiliser le prompt expert
        publication_context=context,
    )
    return result


async def build_publication_context(publication) -> str:
    """Construit le contexte complet d'une publication pour l'IA.

    Inclut le contenu HTML, le resume, et le texte PDF si disponible.
    """
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

    # Tenter d'extraire le texte PDF si disponible
    pdf_text = None
    if publication.pdf_url:
        pdf_text = await extract_pdf_text(publication.pdf_url)
        if pdf_text:
            parts.append(f"Document PDF :\n{pdf_text[:8000]}")

    return "\n\n".join(parts)
