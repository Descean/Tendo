"""Analyseur de Journaux Nationaux des Marches Publics (JNMP).

Chaque journal JNMP est un recueil PDF contenant plusieurs documents
individuels (avis d'appel a concurrence, PV d'ouverture/attribution,
addendums, AMI, etc.). Ce module :

  1. Telecharge le PDF du journal
  2. Extrait le texte page par page via pypdf
  3. Parse la table des matieres (page 1) pour les frontieres de rubriques
  4. Detecte les documents individuels dans chaque rubrique :
     - Pages texte : marqueurs "SECTION 0", "Reference SIGMAP", "REPUBLIQUE DU BENIN"
     - Pages scan : analyse visuelle du bas de page (signature = fin de document)
  5. Extrait les metadonnees (autorite, reference, objet, montant, deadline)
  6. Genere un sous-PDF par document individuel
  7. Retourne une liste de publications prete a etre inseree en base
"""

import asyncio
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select, and_

from app.utils.logger import logger

# Repertoire de stockage des sous-PDFs extraits
PDF_EXTRACTS_DIR = Path(__file__).parent.parent / "data" / "pdf_extracts"
PDF_EXTRACTS_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Dictionnaire des rubriques → types de documents
# ─────────────────────────────────────────────────────────────────────────────
RUBRICS: Dict[str, str] = {
    "avis d'appel à concurrence": "AAO",
    "avis d'appel à la concurrence": "AAO",
    "avis d'appel d'offres": "AAO",
    "appel d'offres ouvert": "AAO",
    "appel d'offres restreint": "AOR",
    "appel à concurrence": "AAO",
    "procès verbal d'ouverture": "PV_OUVERTURE",
    "procès-verbal d'ouverture": "PV_OUVERTURE",
    "p.v. d'ouverture": "PV_OUVERTURE",
    "pv d'ouverture": "PV_OUVERTURE",
    "procès verbal d'attribution": "PV_ATTRIBUTION",
    "procès-verbal d'attribution": "PV_ATTRIBUTION",
    "p.v. d'attribution": "PV_ATTRIBUTION",
    "pv d'attribution": "PV_ATTRIBUTION",
    "résultats d'évaluation": "PV_ATTRIBUTION",
    "résultat d'évaluation": "PV_ATTRIBUTION",
    "avis d'attribution définitive": "AVIS_ATTRIBUTION",
    "avis d'attribution": "AVIS_ATTRIBUTION",
    "attribution définitive": "AVIS_ATTRIBUTION",
    "constitution de liste restreinte": "LISTE_RESTREINTE",
    "liste restreinte": "LISTE_RESTREINTE",
    "appel à manifestation d'intérêt": "AMI",
    "manifestation d'intérêt": "AMI",
    "appel a manifestation": "AMI",
    "addendum": "ADDITIF",
    "additif": "ADDITIF",
    "rectificatif": "ADDITIF",
    "engagement précoce du marché": "AMI",
    "demande de renseignements et de prix": "RFQ",
    "demande de propositions": "RFP",
    "demande de cotation": "RFQ",
    "avis de non-objection": "DECISION_ARMP",
    "décision d'approbation": "DECISION_ARMP",
    "décision": "DECISION_ARMP",
}

# Label lisible par type pour construire le titre
TYPE_LABELS: Dict[str, str] = {
    "AAO": "Avis d'Appel à Concurrence",
    "AOR": "Appel d'Offres Restreint",
    "PV_OUVERTURE": "PV d'Ouverture des Plis",
    "PV_ATTRIBUTION": "PV d'Attribution",
    "AVIS_ATTRIBUTION": "Avis d'Attribution Définitive",
    "LISTE_RESTREINTE": "Constitution de Liste Restreinte",
    "AMI": "Appel à Manifestation d'Intérêt",
    "ADDITIF": "Addendum/Additif",
    "RFQ": "Demande de Devis",
    "RFP": "Demande de Propositions",
    "DECISION_ARMP": "Décision ARMP",
}

# Patterns regex pour l'extraction des metadonnees
PATTERNS: Dict[str, re.Pattern] = {
    "autorite": re.compile(
        r"(?:Autorit[eé]\s+contractante|Personne\s+Responsable[^:\n]*|PRMP\s+p\.?\s*i\.?)\s*[:\-]\s*(.+?)(?=\n\n|\n[A-Z]|Objet\s*:|\Z)",
        re.I | re.DOTALL,
    ),
    "reference_sigmap": re.compile(
        r"(?:R[eé]f[eé]rence\s+SIGMAP|Ref\.\s*SIGMAP|SIGMAP)\s*[:\-]\s*([A-Z0-9/_\-\.]+)",
        re.I,
    ),
    "reference": re.compile(
        r"(?:R[eé]f[eé]rence|N[°º]\s*d[''e]\s*dossier|Num[eé]ro\s+d[''e]\s*dossier)\s*[:\-]?\s*([A-Z0-9/_\-\.]{5,})",
        re.I,
    ),
    "objet": re.compile(
        r"Objet\s*(?:du\s+march[eé])?\s*[:\-]\s*(.+?)(?=\n\n|\n(?:Autorit|R[eé]f|Budget|Date|Source|Financement|Montant|Mode|Type|Lieu)|\Z)",
        re.I | re.DOTALL,
    ),
    "montant": re.compile(
        r"(?:montant|budget\s+estim[eé]|valeur)\s*(?:du\s+march[eé]|estim[eé])?\s*(?:est\s+de|s['']?[eé]l[eè]ve\s+[àa])?\s*[:\-]?\s*([\d\s]+(?:[,\.]\d+)?)\s*(?:F\s*CFA|FCFA|XOF)",
        re.I,
    ),
    "deadline": re.compile(
        r"(?:date\s+limite|d[eé]lai\s+de\s+(?:d[eé]p[oô]t|remise|soumission)|date\s+de\s+cl[oô]ture)\s*[:\-]\s*(.+?)(?=\n|$)",
        re.I,
    ),
    "attributaire": re.compile(
        r"(?:attributaire|titulaire)\s*(?:d[eé]finitif|provisoire)?\s*[:\-]\s*(.+?)(?=\n|$)",
        re.I,
    ),
    "financement": re.compile(
        r"(?:financement|source\s+de\s+financement|bailleur)\s*[:\-]\s*(.+?)(?=\n|$)",
        re.I,
    ),
    "date_publication": re.compile(
        r"(?:publi[eé]\s+le|paru\s+le|parution\s+du)\s*[:\-]?\s*(\d{1,2}\s+\w+\s+\d{4})",
        re.I,
    ),
}

# Marqueurs de debut de document (texte extractible)
DOC_START_PATTERNS = [
    re.compile(r"R[EÉ]PUBLIQUE\s+DU\s+B[EÉ]NIN", re.I),
    re.compile(r"SECTION\s+\d+\.\s+AVIS", re.I),
    re.compile(r"R[eé]f[eé]rence\s+SIGMAP\s*:", re.I),
    re.compile(r"PROC[EÈ]S[\s\-]VERBAL\s+D[''']", re.I),
    re.compile(r"ADDENDUM\s+N", re.I),
]


# ─────────────────────────────────────────────────────────────────────────────
# Extraction de texte via pypdf (page par page)
# ─────────────────────────────────────────────────────────────────────────────

async def _download_pdf(url: str, retries: int = 3) -> Optional[bytes]:
    """Telecharge un PDF depuis son URL avec retry et fallback IPv4."""
    for attempt in range(retries):
        try:
            transport = httpx.AsyncHTTPTransport(
                retries=2,
                local_address="0.0.0.0",
            )
            async with httpx.AsyncClient(
                timeout=90,
                follow_redirects=True,
                transport=transport,
            ) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/120.0"
                })
                if resp.status_code == 200:
                    return resp.content
                logger.warning(f"[JNMPAnalyzer] HTTP {resp.status_code} pour {url}")
        except Exception as e:
            logger.warning(f"[JNMPAnalyzer] Download tentative {attempt+1}/{retries}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(5)
    logger.error(f"[JNMPAnalyzer] Echec download apres {retries} tentatives: {url}")
    return None


def _extract_pages_text(pdf_bytes: bytes) -> List[str]:
    """Extrait le texte de chaque page avec pypdf."""
    from pypdf import PdfReader

    pages_text: List[str] = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page in reader.pages:
            text = page.extract_text() or ""
            pages_text.append(text)
        logger.info(f"[JNMPAnalyzer] pypdf: {len(pages_text)} pages extraites")
    except Exception as e:
        logger.error(f"[JNMPAnalyzer] Erreur extraction pypdf: {e}")
    return pages_text


# ─────────────────────────────────────────────────────────────────────────────
# Extraction sous-PDF via conversion image (leger)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_sub_pdf(pdf_bytes: bytes, page_start: int, page_end: int, reference: str) -> Optional[str]:
    """Extrait un sous-PDF via conversion image (pages start..end incluses).

    Utilise pdf2image + Pillow pour produire des PDFs legers (~300 Ko/page)
    au lieu de pypdf qui copie les ressources lourdes (~10+ Mo/page).
    """
    from pdf2image import convert_from_bytes
    from PIL import Image

    try:
        first_page = page_start + 1  # pdf2image 1-indexed
        last_page = page_end + 1

        images = convert_from_bytes(
            pdf_bytes,
            dpi=200,
            first_page=first_page,
            last_page=last_page,
            fmt="jpeg",
        )

        if not images:
            logger.warning(f"[JNMPAnalyzer] Aucune page extraite pour {reference}")
            return None

        rgb_images = [img.convert("RGB") for img in images]

        safe_name = re.sub(r'[^\w\-.]', '_', reference) + ".pdf"
        filepath = PDF_EXTRACTS_DIR / safe_name

        rgb_images[0].save(
            filepath,
            "PDF",
            save_all=True,
            append_images=rgb_images[1:] if len(rgb_images) > 1 else [],
            quality=85,
        )

        pages_written = len(rgb_images)
        size_mb = filepath.stat().st_size / (1024 * 1024)
        logger.info(f"[JNMPAnalyzer] Sous-PDF: {safe_name} ({pages_written}p, {size_mb:.1f}Mo)")
        return safe_name
    except Exception as e:
        logger.warning(f"[JNMPAnalyzer] Erreur extraction sous-PDF {reference}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Parse de la table des matieres (page 1)
# ─────────────────────────────────────────────────────────────────────────────

# Ordre important : les patterns plus specifiques en premier
TOC_RUBRIC_PATTERNS = [
    ("avis d'attribution", "AVIS_ATTRIBUTION"),
    ("procès verbal d'attribution", "PV_ATTRIBUTION"),
    ("procès-verbal d'attribution", "PV_ATTRIBUTION"),
    ("résultat d'évaluation", "PV_ATTRIBUTION"),
    ("résultat", "PV_ATTRIBUTION"),
    ("attribution", "PV_ATTRIBUTION"),
    ("procès verbal d'ouverture", "PV_OUVERTURE"),
    ("procès-verbal d'ouverture", "PV_OUVERTURE"),
    ("ouverture", "PV_OUVERTURE"),
    ("avis d'appel", "AAO"),
    ("appel à concurrence", "AAO"),
    ("appel d'offres", "AAO"),
    ("liste restreinte", "LISTE_RESTREINTE"),
    ("cotation", "RFQ"),
    ("demande de renseignements", "RFQ"),
    ("manifestation", "AMI"),
    ("addendum", "ADDITIF"),
    ("additif", "ADDITIF"),
    ("autres", "AUTRES"),
]


def _parse_toc(pages_text: List[str]) -> List[Tuple[int, str]]:
    """Parse la table des matieres de la page 1.

    Le format TOC du JNMP est typiquement :
        * Avis d'appel a concurrence
        (Page 2)
        * Proces verbal d'ouverture
        (Page 34)

    Le texte de rubrique et "(Page XX)" peuvent etre sur la meme ligne
    ou sur des lignes separees.

    Returns:
        Liste de (page_0indexed, doc_type) triee par page.
    """
    if not pages_text:
        return []

    toc_text = pages_text[0]
    entries: List[Tuple[int, str]] = []

    # Strategie : collecter les lignes de texte, puis associer chaque
    # "(Page XX)" a la derniere ligne de texte vue avant.
    lines = toc_text.split("\n")
    last_rubric_text = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Chercher "(Page XX)" dans cette ligne
        page_match = re.search(r"\((?:Page|page)\s*(\d+)\)", stripped)

        if page_match:
            page_num = int(page_match.group(1)) - 1  # 0-indexed

            # Le texte de rubrique peut etre :
            # 1. Avant "(Page XX)" sur la meme ligne
            # 2. Sur la ligne precedente (last_rubric_text)
            rubric_before = stripped[:page_match.start()].strip()
            if rubric_before and len(rubric_before) > 3:
                rubric_text = rubric_before.lower()
            else:
                rubric_text = last_rubric_text.lower()

            # Nettoyer le prefixe "*" ou "-"
            rubric_text = re.sub(r"^[\*\-\s]+", "", rubric_text).strip()

            doc_type = "AAO"  # Default
            for pattern, dtype in TOC_RUBRIC_PATTERNS:
                if pattern in rubric_text:
                    doc_type = dtype
                    break

            entries.append((page_num, doc_type))
            logger.debug(f"[TOC] Rubrique: '{rubric_text}' -> {doc_type} (page {page_num + 1})")
            last_rubric_text = ""
        else:
            # Ligne de texte sans "(Page XX)" — memoriser comme rubrique potentielle
            last_rubric_text = stripped

    entries.sort(key=lambda x: x[0])
    logger.info(f"[JNMPAnalyzer] TOC parsed: {len(entries)} rubriques: {[(e[1], e[0]+1) for e in entries]}")
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# Detection visuelle des fins de document (pages scannees)
# ─────────────────────────────────────────────────────────────────────────────

def _is_last_page_visual(pdf_bytes: bytes, page_idx: int) -> bool:
    """Detecte si une page scannee est la derniere d'un document.

    Heuristique : les dernieres pages ont un bas de page vide (apres la signature).
    On analyse la densite de pixels sombres dans le quart inferieur.
    """
    from pdf2image import convert_from_bytes
    from PIL import Image

    try:
        images = convert_from_bytes(
            pdf_bytes, dpi=72,
            first_page=page_idx + 1,
            last_page=page_idx + 1,
            fmt="jpeg",
        )
        if not images:
            return False

        gray = images[0].convert("L")
        w, h = gray.size
        pixels = list(gray.getdata())

        # Analyser le dernier quart (25%) de la page en 3 bandes
        band_h = h // 12  # ~8% chaque
        bottom_bands = []
        for b in range(9, 12):  # Bandes 9, 10, 11 = dernier quart
            y0 = b * band_h
            y1 = min((b + 1) * band_h, h)
            dark = 0
            total = 0
            for y in range(y0, y1):
                for x in range(0, w, 4):
                    total += 1
                    if pixels[y * w + x] < 140:
                        dark += 1
            bottom_bands.append(dark / total if total else 0)

        # Derniere page : les 2 dernieres bandes ont une densite < 2%
        # (= espace blanc apres la signature)
        is_last = bottom_bands[-1] < 0.02 and bottom_bands[-2] < 0.03

        return is_last
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Detection textuelle des debuts de document
# ─────────────────────────────────────────────────────────────────────────────

def _is_doc_start_text(page_text: str) -> bool:
    """Detecte si une page texte commence un nouveau document."""
    text = page_text[:500]  # Regarder seulement le debut
    for pattern in DOC_START_PATTERNS:
        if pattern.search(text):
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Segmentation intelligente en documents individuels
# ─────────────────────────────────────────────────────────────────────────────

MAX_DOC_PAGES = 8  # Max pages par document (securite)
MIN_TEXT_CHARS = 50  # Seuil texte vs scan


def _segment_documents_v2(
    pages_text: List[str],
    pdf_bytes: bytes,
    toc_entries: List[Tuple[int, str]],
) -> List[Dict]:
    """Segmentation avancee : detecte les documents individuels dans chaque rubrique.

    Strategie :
    1. La TOC definit les frontieres de rubriques
    2. Dans chaque rubrique, on detecte les documents individuels :
       - Pages texte : marqueurs de debut de document
       - Pages scan : analyse visuelle du bas de page (fin = signature)
    3. Fallback : max MAX_DOC_PAGES pages par document
    """
    total_pages = len(pages_text)

    if not toc_entries:
        # Pas de TOC : fallback sur l'ancienne methode (tout = 1 doc)
        return [{
            "type": "AAO",
            "pages": (0, total_pages - 1),
            "text": "\n".join(pages_text),
            "rubric_name": None,
        }]

    # Construire la liste des rubriques avec leurs ranges de pages
    rubric_ranges: List[Tuple[int, int, str]] = []
    for i, (start_page, doc_type) in enumerate(toc_entries):
        if i + 1 < len(toc_entries):
            end_page = toc_entries[i + 1][0] - 1
        else:
            end_page = total_pages - 1
        rubric_ranges.append((start_page, end_page, doc_type))

    documents: List[Dict] = []

    for rubric_start, rubric_end, rubric_type in rubric_ranges:
        if rubric_type == "AUTRES":
            continue  # Skip "Autres" rubric

        # Identifier les pages de contenu (exclure les pages de titre "Rubrique")
        content_pages = []
        for p in range(rubric_start, rubric_end + 1):
            if p >= total_pages:
                break
            text = pages_text[p].strip()
            # Exclure la page de titre de rubrique et la page editoriale
            if text.startswith("Rubrique") and len(text) < 100:
                continue
            if text.startswith("Chers lecteurs"):
                continue
            if p == 0:  # Page TOC
                continue
            content_pages.append(p)

        if not content_pages:
            continue

        # Detecter les debuts de documents dans cette rubrique
        doc_boundaries = _find_doc_boundaries(
            content_pages, pages_text, pdf_bytes
        )

        # Creer les documents
        for i, start_p in enumerate(doc_boundaries):
            if i + 1 < len(doc_boundaries):
                end_p = doc_boundaries[i + 1] - 1
            else:
                end_p = content_pages[-1]

            # Limiter la taille
            if end_p - start_p + 1 > MAX_DOC_PAGES:
                end_p = start_p + MAX_DOC_PAGES - 1

            # Collecter le texte
            doc_text_parts = []
            for p in range(start_p, end_p + 1):
                if p < total_pages:
                    doc_text_parts.append(pages_text[p])
            doc_text = "\n".join(doc_text_parts)

            documents.append({
                "type": rubric_type,
                "pages": (start_p, end_p),
                "text": doc_text,
                "rubric_name": TYPE_LABELS.get(rubric_type, rubric_type),
            })

    logger.info(f"[JNMPAnalyzer] {len(documents)} documents individuels detectes")
    return documents


def _find_doc_boundaries(
    content_pages: List[int],
    pages_text: List[str],
    pdf_bytes: bytes,
) -> List[int]:
    """Trouve les pages de debut de chaque document dans un ensemble de pages.

    Returns:
        Liste de numeros de page (0-indexed) ou commence chaque document.
    """
    if not content_pages:
        return []

    boundaries = [content_pages[0]]  # Le premier document commence toujours
    after_last_page = False

    for i, page_idx in enumerate(content_pages):
        if i == 0:
            continue  # Deja ajouté

        text = pages_text[page_idx].strip()
        is_text_page = len(text) > MIN_TEXT_CHARS
        prev_page = content_pages[i - 1]

        # Cas 1 : nouvelle page texte avec marqueur de debut de document
        if is_text_page and _is_doc_start_text(text):
            if page_idx not in boundaries:
                boundaries.append(page_idx)
                after_last_page = False
                continue

        # Cas 2 : apres une derniere page detectee visuellement
        if after_last_page:
            if page_idx not in boundaries:
                boundaries.append(page_idx)
            after_last_page = False
            continue

        # Cas 3 : transition scan → texte ou texte → scan = probable nouveau doc
        prev_text = pages_text[prev_page].strip()
        prev_is_text = len(prev_text) > MIN_TEXT_CHARS
        if is_text_page != prev_is_text and not after_last_page:
            if page_idx not in boundaries:
                boundaries.append(page_idx)
                after_last_page = False
                continue

        # Cas 4 : page scan — verifier si c'est une derniere page (signature)
        if not is_text_page:
            if _is_last_page_visual(pdf_bytes, page_idx):
                after_last_page = True

        # Cas 5 : securite — ne pas depasser MAX_DOC_PAGES depuis le dernier debut
        last_boundary = boundaries[-1]
        if page_idx - last_boundary >= MAX_DOC_PAGES:
            if page_idx not in boundaries:
                boundaries.append(page_idx)

    boundaries.sort()
    return boundaries


# ─────────────────────────────────────────────────────────────────────────────
# Extraction des metadonnees
# ─────────────────────────────────────────────────────────────────────────────

def _extract_metadata(text: str, doc_type: str) -> Dict:
    """Extrait les metadonnees du document par regex."""
    metadata: Dict = {}

    for field, pattern in PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = re.sub(r"\s+", " ", match.group(1)).strip()
            metadata[field] = value

    # Extraction specifique PV : soumissionnaires
    if doc_type in ("PV_OUVERTURE", "PV_ATTRIBUTION"):
        soumissionnaires: List[str] = []
        in_table = False
        for line in text.split("\n"):
            if re.search(r"soumissionnaire|entreprise|firme", line, re.I) and re.search(
                r"n[°º]|ordre|rang", line, re.I
            ):
                in_table = True
                continue
            if in_table:
                stripped = line.strip()
                if stripped and len(stripped) > 5 and not stripped.startswith("-"):
                    soumissionnaires.append(stripped[:200])
                if not stripped:
                    in_table = False
            if len(soumissionnaires) >= 20:
                break
        if soumissionnaires:
            metadata["soumissionnaires"] = soumissionnaires[:10]

    # Pour les addendums : reference de l'avis modifie
    if doc_type == "ADDITIF":
        m = re.search(
            r"(?:avis|DAO|dossier)\s*n[°º]?\s*[:\-]?\s*([A-Z0-9/_\-\.]{5,})",
            text,
            re.I,
        )
        if m:
            metadata["avis_modifie"] = m.group(1)

    return metadata


def _extract_number(amount_str: str) -> Optional[float]:
    """Convertit '1 067 796' -> 1067796.0"""
    digits = re.sub(r"[^\d]", "", amount_str)
    return float(digits) if digits else None


# ─────────────────────────────────────────────────────────────────────────────
# Construction du titre et de la reference
# ─────────────────────────────────────────────────────────────────────────────

def _build_title(doc: Dict, metadata: Dict, journal_num: str, index: int) -> str:
    """Construit un titre lisible pour le document extrait."""
    type_label = TYPE_LABELS.get(doc["type"], doc["type"])

    # Priorite 1 : objet extrait (nettoye)
    if metadata.get("objet"):
        objet = metadata["objet"].strip()
        # Rejeter les objets generiques / templates
        bad_patterns = [
            r"ins[eé]r[eé]",
            r"compte\s+de\s+\(",
            r"\[.*\]",
            r"nom\s+de\s+l",
        ]
        is_bad = any(re.search(p, objet, re.I) for p in bad_patterns)
        if not is_bad and len(objet) > 10:
            return f"{type_label} – {objet}"

    # Priorite 2 : autorite contractante
    if metadata.get("autorite"):
        auth = metadata["autorite"].strip()
        return f"{type_label} – {auth} – JNMP N°{journal_num}"

    # Fallback
    p_start = doc["pages"][0] + 1
    p_end = doc["pages"][1] + 1
    return f"{type_label} – JNMP N°{journal_num} (p.{p_start}-{p_end})"


def _build_reference(metadata: Dict, journal_num: str, doc_type: str, index: int) -> str:
    """Construit une reference unique pour le document."""
    ref = metadata.get("reference_sigmap") or metadata.get("reference")
    if ref and len(ref) >= 5:
        if len(ref) > 200:
            ref = ref[:200]
        return ref
    return f"JNMP-{journal_num}-{doc_type}-{index:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale d'analyse
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_jnmp_pdf(pdf_url: str, journal_num: str) -> List[Dict]:
    """Analyse un journal JNMP et retourne les publications extraites."""
    logger.info(f"[JNMPAnalyzer] Analyse journal N°{journal_num}: {pdf_url}")

    # 1. Telecharger le PDF
    pdf_bytes = await _download_pdf(pdf_url)
    if not pdf_bytes:
        logger.error(f"[JNMPAnalyzer] Echec download journal {journal_num}")
        return []

    # 2. Extraire le texte page par page
    pages_text = _extract_pages_text(pdf_bytes)
    if not pages_text:
        logger.error(f"[JNMPAnalyzer] Aucun texte extrait du journal {journal_num}")
        return []

    total_chars = sum(len(p) for p in pages_text)
    if total_chars < 200:
        logger.warning(
            f"[JNMPAnalyzer] Texte insuffisant ({total_chars} chars) pour journal {journal_num}"
        )
        return []

    # 3. Parser la table des matieres
    toc_entries = _parse_toc(pages_text)
    logger.info(f"[JNMPAnalyzer] TOC: {len(toc_entries)} rubriques")

    # 4. Segmenter en documents individuels
    documents = _segment_documents_v2(pages_text, pdf_bytes, toc_entries)

    # 5. Construire les publications
    publications: List[Dict] = []

    from app.config import settings

    for idx, doc in enumerate(documents, start=1):
        metadata = _extract_metadata(doc["text"], doc["type"])

        reference = _build_reference(metadata, journal_num, doc["type"], idx)
        title = _build_title(doc, metadata, journal_num, idx)
        summary = doc["text"].strip() if doc["text"] else ""

        budget = None
        if metadata.get("montant"):
            budget = _extract_number(metadata["montant"])

        authority_name = metadata.get("autorite", "")
        # Pas de troncature des champs

        financing = metadata.get("financement", "Budget National")

        # Sous-PDF pour ce document
        sub_pdf_url = pdf_url
        sub_pdf_name = _extract_sub_pdf(
            pdf_bytes, doc["pages"][0], doc["pages"][1], reference
        )
        if sub_pdf_name:
            sub_pdf_url = f"{settings.base_url}/api/v1/pdf-extracts/{sub_pdf_name}"

        p_start = doc["pages"][0] + 1
        p_end = doc["pages"][1] + 1

        pub = {
            "source": "JNMP",
            "reference": reference,
            "title": title,
            "summary": summary,
            "budget": budget,
            "deadline": None,
            "pdf_url": sub_pdf_url,
            "pdf_content": doc["text"] if doc["text"] else "",
            "html_content": f"[Extrait JNMP N°{journal_num}, pages {p_start}-{p_end}]",
            "category": "marché",
            "sectors": _detect_sectors(title + " " + summary),
            "regions": _detect_regions(title + " " + summary),
            "published_date": None,
            "authority_name": authority_name if authority_name else None,
            "authority_email": None,
            "document_type": doc["type"],
            "financing_source": financing if financing else "Budget National",
            "country": "Bénin",
        }
        publications.append(pub)

    logger.info(
        f"[JNMPAnalyzer] Journal N°{journal_num}: {len(publications)} publications"
    )
    return publications


# ─────────────────────────────────────────────────────────────────────────────
# Detection secteurs / regions
# ─────────────────────────────────────────────────────────────────────────────

def _detect_sectors(text: str) -> List[str]:
    t = text.lower()
    sectors = []
    if any(w in t for w in ("travaux", "construction", "route", "batiment", "btp", "ouvrage")):
        sectors.append("BTP")
    if any(w in t for w in ("fourniture", "equipement", "materiel", "produit")):
        sectors.append("Fournitures")
    if any(w in t for w in ("service", "prestation", "consultant", "etude", "assistance")):
        sectors.append("Services")
    if any(w in t for w in ("informatique", "numerique", "logiciel", "tic", "système")):
        sectors.append("TIC")
    if any(w in t for w in ("sante", "hôpital", "médical", "pharmacie")):
        sectors.append("Santé")
    if any(w in t for w in ("agriculture", "elevage", "agro", "pêche")):
        sectors.append("Agriculture")
    if any(w in t for w in ("eau", "assainissement", "hydraulique")):
        sectors.append("Eau & Assainissement")
    if any(w in t for w in ("energie", "électricité", "solaire")):
        sectors.append("Énergie")
    return sectors or ["Autres"]


def _detect_regions(text: str) -> List[str]:
    t = text.lower()
    regions = []
    mapping = {
        "Cotonou": ["cotonou"],
        "Porto-Novo": ["porto-novo", "porto novo"],
        "Parakou": ["parakou"],
        "Bohicon": ["bohicon"],
        "Abomey-Calavi": ["calavi", "abomey-calavi"],
        "Abomey": ["abomey"],
        "Natitingou": ["natitingou"],
        "Kandi": ["kandi"],
        "Lokossa": ["lokossa"],
        "National": ["national", "bénin", "benin"],
    }
    for region, keywords in mapping.items():
        if any(kw in t for kw in keywords):
            regions.append(region)
    return regions or ["National"]


# ─────────────────────────────────────────────────────────────────────────────
# Traitement en base de donnees
# ─────────────────────────────────────────────────────────────────────────────

async def process_jnmp_journals(db) -> int:
    """Traite les journaux JNMP non encore segmentes."""
    from app.models.publication import Publication
    import re as _re

    result = await db.execute(
        select(Publication).where(
            and_(
                Publication.source == "JNMP",
                Publication.pdf_url.is_not(None),
                Publication.is_processed == False,
                ~Publication.reference.like("JNMP-%"),
                Publication.title.like("Journal National%"),
            )
        ).limit(5)
    )
    journals = result.scalars().all()

    if not journals:
        logger.info("[JNMPAnalyzer] Aucun journal JNMP a traiter")
        return 0

    logger.info(f"[JNMPAnalyzer] {len(journals)} journaux a traiter")
    total_created = 0

    for journal in journals:
        journal_num = "0"
        m = _re.search(r"N[°º]?\s*(\d+)", journal.title or "", _re.I)
        if m:
            journal_num = m.group(1)
        else:
            m2 = _re.search(r"journal(\d+)", journal.pdf_url or "", _re.I)
            if m2:
                journal_num = m2.group(1)

        try:
            publications = await analyze_jnmp_pdf(journal.pdf_url, journal_num)

            created = 0
            for pub_data in publications:
                existing = await db.execute(
                    select(Publication).where(
                        Publication.reference == pub_data["reference"]
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                new_pub = Publication(
                    source=pub_data["source"],
                    reference=pub_data["reference"],
                    title=pub_data["title"],
                    summary=pub_data.get("summary", ""),
                    budget=pub_data.get("budget"),
                    deadline=pub_data.get("deadline"),
                    pdf_url=pub_data.get("pdf_url"),
                    html_content=pub_data.get("html_content", ""),
                    category=pub_data.get("category", "marché"),
                    sectors=pub_data.get("sectors", []),
                    regions=pub_data.get("regions", []),
                    published_date=pub_data.get("published_date"),
                    authority_name=pub_data.get("authority_name"),
                    authority_email=pub_data.get("authority_email"),
                    document_type=pub_data.get("document_type"),
                    financing_source=pub_data.get("financing_source"),
                    country=pub_data.get("country", "Bénin"),
                    is_processed=False,
                )
                db.add(new_pub)
                created += 1

            journal.is_processed = True
            journal.html_content = (
                f"[SEGMENTE: {created} documents depuis JNMP N°{journal_num}]"
            )

            await db.commit()
            total_created += created
            logger.info(
                f"[JNMPAnalyzer] Journal N°{journal_num}: {created} publications creees"
            )

            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"[JNMPAnalyzer] Erreur journal {journal.id}: {e}")
            await db.rollback()
            journal.is_processed = True
            await db.commit()

    logger.info(f"[JNMPAnalyzer] Total: {total_created} publications creees")
    return total_created
