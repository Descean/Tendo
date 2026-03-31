"""Analyseur de Journaux Nationaux des Marches Publics (JNMP).

Chaque journal JNMP est un recueil PDF contenant plusieurs documents
individuels (avis d'appel a concurrence, PV d'ouverture/attribution,
addendums, AMI, etc.). Ce module :

  1. Telecharge le PDF du journal
  2. Extrait le texte page par page via pypdf
  3. Detecte les rubriques (en-tetes de section)
  4. Segmente le PDF en documents individuels
  5. Extrait les metadonnees (autorite, reference, objet, montant, deadline)
  6. Retourne une liste de publications prete a etre inseree en base

Utilise uniquement pypdf (deja installe) - pas besoin de PyMuPDF/fitz.
"""

import asyncio
import hashlib
import io
import re
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select, and_

from app.utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Dictionnaire des rubriques → types de documents
# La cle est la sous-chaine cherchee (en minuscules) dans chaque ligne.
# La valeur est le document_type a stocker en base.
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
    "RFQ": "Demande de Renseignements et de Prix",
    "RFP": "Demande de Propositions",
    "DECISION_ARMP": "Décision ARMP",
}

# ─────────────────────────────────────────────────────────────────────────────
# Patterns regex pour l'extraction des metadonnees
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Extraction de texte via pypdf (page par page)
# ─────────────────────────────────────────────────────────────────────────────

async def _download_pdf(url: str) -> Optional[bytes]:
    """Telecharge un PDF depuis son URL."""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                return resp.content
            logger.warning(f"[JNMPAnalyzer] HTTP {resp.status_code} pour {url}")
    except Exception as e:
        logger.error(f"[JNMPAnalyzer] Erreur download {url}: {e}")
    return None


def _extract_pages_text(pdf_bytes: bytes) -> List[str]:
    """Extrait le texte de chaque page avec pypdf. Retourne une liste de textes."""
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
# Detection des rubriques
# ─────────────────────────────────────────────────────────────────────────────

def _detect_rubrics(pages_text: List[str]) -> List[Tuple[int, int, str, str]]:
    """Detecte les rubriques dans le texte page par page.

    Returns:
        Liste de (page_idx, line_num, rubric_text, doc_type) triee par position.
    """
    found: List[Tuple[int, int, str, str]] = []

    for page_idx, page_text in enumerate(pages_text):
        lines = page_text.split("\n")
        for line_num, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped or len(line_stripped) < 5:
                continue
            line_lower = line_stripped.lower()

            for rubric_kw, doc_type in RUBRICS.items():
                if rubric_kw in line_lower:
                    # Eviter les doublons sur la meme page+ligne
                    already = any(
                        f[0] == page_idx and f[1] == line_num for f in found
                    )
                    if not already:
                        found.append((page_idx, line_num, line_stripped, doc_type))
                    break

    found.sort(key=lambda x: (x[0], x[1]))
    logger.info(f"[JNMPAnalyzer] {len(found)} rubriques detectees")
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Segmentation en documents individuels
# ─────────────────────────────────────────────────────────────────────────────

def _segment_documents(
    pages_text: List[str],
    rubric_positions: List[Tuple[int, int, str, str]],
) -> List[Dict]:
    """Decoupe les pages en documents individuels selon les rubriques.

    Chaque document contient :
      type, pages (start, end), text, rubric_name
    """
    if not rubric_positions:
        # Pas de rubriques detectees : tout le journal = un seul document inconnu
        return [
            {
                "type": "AAO",
                "pages": (0, len(pages_text) - 1),
                "text": "\n".join(pages_text),
                "rubric_name": None,
            }
        ]

    documents: List[Dict] = []

    for i, (p_start, l_start, rubric_name, doc_type) in enumerate(rubric_positions):
        # Calculer la fin du document
        if i + 1 < len(rubric_positions):
            p_end, l_end, _, _ = rubric_positions[i + 1]
        else:
            p_end = len(pages_text) - 1
            l_end = None  # Jusqu'a la fin du PDF

        # Collecter le texte du document
        text_parts: List[str] = []

        if p_start == p_end:
            # Tout sur la meme page
            page_lines = pages_text[p_start].split("\n")
            end_line = l_end if l_end is not None else len(page_lines)
            text_parts.append("\n".join(page_lines[l_start:end_line]))
        else:
            # Premiere page : depuis la ligne de la rubrique jusqu'a la fin
            page_lines = pages_text[p_start].split("\n")
            text_parts.append("\n".join(page_lines[l_start:]))

            # Pages intermediaires completes
            for p in range(p_start + 1, p_end):
                text_parts.append(pages_text[p])

            # Derniere page : depuis le debut jusqu'a la ligne de la prochaine rubrique
            if p_end < len(pages_text):
                page_lines = pages_text[p_end].split("\n")
                end_line = l_end if l_end is not None else len(page_lines)
                text_parts.append("\n".join(page_lines[:end_line]))

        doc_text = "\n".join(text_parts)

        documents.append(
            {
                "type": doc_type,
                "pages": (p_start, p_end),
                "text": doc_text,
                "rubric_name": rubric_name,
            }
        )

    return documents


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
            # Truncater les valeurs tres longues
            metadata[field] = value[:500]

    # Extraction specifique PV : liste simplifiee des soumissionnaires
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
                # Ligne de tableau potentielle
                stripped = line.strip()
                if stripped and len(stripped) > 5 and not stripped.startswith("-"):
                    # Eviter les entetes et lignes trop courtes
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
# Construction du titre pour un document extrait
# ─────────────────────────────────────────────────────────────────────────────

def _build_title(doc: Dict, metadata: Dict, journal_num: str, index: int) -> str:
    """Construit un titre lisible pour le document extrait."""
    type_label = TYPE_LABELS.get(doc["type"], doc["type"])

    # Priorite 1 : objet extrait
    if metadata.get("objet"):
        objet = metadata["objet"].strip()
        if len(objet) > 10:
            return f"{type_label} – {objet[:300]}"

    # Priorite 2 : rubrique detaille
    if doc.get("rubric_name"):
        return f"{type_label} – JNMP N°{journal_num} (doc {index})"

    return f"{type_label} – JNMP N°{journal_num} (doc {index})"


def _build_reference(metadata: Dict, journal_num: str, doc_type: str, index: int) -> str:
    """Construit une reference unique pour le document."""
    # Priorite 1 : reference SIGMAP
    ref = metadata.get("reference_sigmap") or metadata.get("reference")
    if ref and len(ref) >= 5:
        # Hacher si trop long pour 255 chars
        if len(ref) > 200:
            ref = ref[:200]
        return ref

    # Fallback : cle composite
    return f"JNMP-{journal_num}-{doc_type}-{index:03d}"


# ─────────────────────────────────────────────────────────────────────────────
# Fonction principale d'analyse
# ─────────────────────────────────────────────────────────────────────────────

async def analyze_jnmp_pdf(pdf_url: str, journal_num: str) -> List[Dict]:
    """Analyse un journal JNMP et retourne les publications extraites.

    Args:
        pdf_url: URL du PDF du journal
        journal_num: Numero du journal (ex: "618")

    Returns:
        Liste de dicts prets a creer comme Publication en base.
        Champs : source, reference, title, summary, budget, deadline,
                 pdf_url, document_type, sectors, regions, authority_name,
                 financing_source, country, html_content
    """
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

    # Verifier qu'on a du contenu
    total_chars = sum(len(p) for p in pages_text)
    if total_chars < 200:
        logger.warning(
            f"[JNMPAnalyzer] Texte insuffisant ({total_chars} chars) pour journal {journal_num}"
        )
        return []

    # 3. Detecter les rubriques
    rubric_positions = _detect_rubrics(pages_text)

    # 4. Segmenter en documents
    documents = _segment_documents(pages_text, rubric_positions)
    logger.info(f"[JNMPAnalyzer] {len(documents)} documents segmentes")

    # 5. Construire les publications
    publications: List[Dict] = []

    for idx, doc in enumerate(documents, start=1):
        if not doc["text"] or len(doc["text"].strip()) < 30:
            continue

        metadata = _extract_metadata(doc["text"], doc["type"])

        reference = _build_reference(metadata, journal_num, doc["type"], idx)
        title = _build_title(doc, metadata, journal_num, idx)
        summary = doc["text"][:800].strip()

        # Budget
        budget = None
        if metadata.get("montant"):
            budget = _extract_number(metadata["montant"])

        # Autorite contractante
        authority_name = metadata.get("autorite", "")
        if authority_name:
            authority_name = authority_name[:255]

        # Financement
        financing = metadata.get("financement", "Budget National")

        pub = {
            "source": "JNMP",
            "reference": reference,
            "title": title[:500],
            "summary": summary,
            "budget": budget,
            "deadline": None,  # Parsing de date complexe, laisse a None
            "pdf_url": pdf_url,
            "html_content": f"[Extrait JNMP N°{journal_num}, pages {doc['pages'][0]+1}-{doc['pages'][1]+1}]",
            "category": "marché",
            "sectors": _detect_sectors(title + " " + summary),
            "regions": _detect_regions(title + " " + summary),
            "published_date": None,
            "authority_name": authority_name[:255] if authority_name else None,
            "authority_email": None,
            "document_type": doc["type"],
            "financing_source": financing[:100] if financing else "Budget National",
            "country": "Bénin",
        }
        publications.append(pub)

    logger.info(
        f"[JNMPAnalyzer] Journal N°{journal_num}: {len(publications)} publications extraites"
    )
    return publications


# ─────────────────────────────────────────────────────────────────────────────
# Detection secteurs / regions (reutilise la logique du scraper)
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
    """Traite les journaux JNMP non encore segmentes.

    - Selectionne les publications JNMP avec pdf_url et is_processed=False
    - Analyse chaque journal et cree des publications individuelles
    - Marque le journal parent comme is_processed=True

    Returns:
        Nombre de documents individuels crees.
    """
    from app.models.publication import Publication
    import re as _re

    # Selectionner les journaux non traites
    result = await db.execute(
        select(Publication).where(
            and_(
                Publication.source == "JNMP",
                Publication.pdf_url.is_not(None),
                Publication.is_processed == False,
            )
        ).limit(5)  # Traiter 5 a la fois pour ne pas saturer
    )
    journals = result.scalars().all()

    if not journals:
        logger.info("[JNMPAnalyzer] Aucun journal JNMP a traiter")
        return 0

    logger.info(f"[JNMPAnalyzer] {len(journals)} journaux a traiter")
    total_created = 0

    for journal in journals:
        # Extraire le numero du journal depuis le titre ou l'URL
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
                # Verifier si la publication existe deja
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

            # Marquer le journal parent comme traite
            journal.is_processed = True
            journal.html_content = (
                f"[SEGMENTE: {created} documents extraits depuis JNMP N°{journal_num}]"
            )

            await db.commit()
            total_created += created
            logger.info(
                f"[JNMPAnalyzer] Journal N°{journal_num}: {created} publications creees"
            )

            # Pause pour ne pas surcharger le serveur
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"[JNMPAnalyzer] Erreur journal {journal.id}: {e}")
            await db.rollback()
            # Marquer quand meme pour eviter de retraiter indefiniment
            journal.is_processed = True
            await db.commit()

    logger.info(f"[JNMPAnalyzer] Total: {total_created} publications creees")
    return total_created
