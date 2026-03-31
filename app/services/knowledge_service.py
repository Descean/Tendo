"""Service de gestion de la base de connaissances metier de Tendo.

Permet de :
- Charger les connaissances initiales (procedures, lexique, reglementation)
- Rechercher des connaissances par mots-cles
- Enrichir les reponses IA avec le contexte metier
"""

from typing import List, Optional

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.utils.logger import logger


async def search_knowledge(
    db: AsyncSession,
    query: str,
    category: str = None,
    country: str = None,
    limit: int = 5,
) -> List[KnowledgeBase]:
    """Recherche dans la base de connaissances par mots-cles."""
    stmt = select(KnowledgeBase).where(KnowledgeBase.is_active == True)

    if category:
        stmt = stmt.where(KnowledgeBase.category == category)
    if country:
        stmt = stmt.where(
            or_(KnowledgeBase.country == country, KnowledgeBase.country == None)
        )

    # Recherche dans le titre, contenu et mots-cles
    query_lower = query.lower()
    stmt = stmt.where(
        or_(
            func.lower(KnowledgeBase.title).contains(query_lower),
            func.lower(KnowledgeBase.content).contains(query_lower),
        )
    ).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_knowledge_context(
    db: AsyncSession,
    document_type: str = None,
    sectors: list = None,
    country: str = "Benin",
) -> str:
    """Construit un contexte de connaissances pour enrichir les reponses IA.

    Retourne un texte structure que l'IA peut utiliser pour ses reponses.
    """
    context_parts = []

    # 1. Connaissances sur le type de document
    if document_type:
        docs = await search_knowledge(db, document_type, category="document_type")
        for doc in docs:
            context_parts.append(f"[{doc.title}] {doc.content[:500]}")

    # 2. Connaissances sur les procedures
    procs = await search_knowledge(db, "procedure", category="procedure", country=country, limit=3)
    for proc in procs:
        context_parts.append(f"[Procedure] {proc.content[:300]}")

    # 3. Connaissances reglementaires
    regs = await search_knowledge(db, "reglementation", category="reglementation", country=country, limit=2)
    for reg in regs:
        context_parts.append(f"[Reglementation] {reg.content[:300]}")

    return "\n\n".join(context_parts) if context_parts else ""


async def seed_initial_knowledge(db: AsyncSession):
    """Peuple la base de connaissances avec les donnees initiales.

    A executer une seule fois lors du setup.
    """
    # Verifier si deja peuple
    count = await db.execute(select(func.count(KnowledgeBase.id)))
    if count.scalar() > 0:
        logger.info("[Knowledge] Base deja peuplee")
        return

    logger.info("[Knowledge] Peuplement de la base de connaissances...")

    entries = _get_initial_knowledge()
    for entry in entries:
        kb = KnowledgeBase(**entry)
        db.add(kb)

    await db.commit()
    logger.info(f"[Knowledge] {len(entries)} entrees ajoutees")


def _get_initial_knowledge() -> list:
    """Retourne les connaissances initiales a inserer."""
    return [
        # ── Types de documents ──
        {
            "category": "document_type",
            "subcategory": "AAO",
            "title": "Avis d'Appel d'Offres (AAO)",
            "content": (
                "Un Avis d'Appel d'Offres est une annonce synthetique publiee par une autorite "
                "contractante pour informer les entreprises d'un marche a attribuer. Il contient : "
                "l'objet du marche, la reference, la date limite de depot, le budget estime, "
                "les conditions de participation et les modalites d'obtention du DAO. "
                "Les variantes incluent : AON (national), AOI (international), AOO (ouvert), "
                "AOR (restreint). Un soumissionnaire doit analyser rapidement l'AAO pour evaluer "
                "son eligibilite et les delais."
            ),
            "keywords": ["avis", "appel d'offres", "AAO", "AON", "AOI", "tender notice"],
            "country": None,
            "language": "fr",
        },
        {
            "category": "document_type",
            "subcategory": "DAO",
            "title": "Dossier d'Appel d'Offres (DAO)",
            "content": (
                "Le DAO est l'ensemble des pieces de consultation remises aux soumissionnaires. "
                "Il comprend : les Instructions aux Soumissionnaires (IS), le Cahier des Charges "
                "Techniques (CCT), les Conditions Generales et Particulieres du contrat, le "
                "Bordereau de Prix Unitaires (BPU), le Detail Estimatif (DE), les formulaires "
                "administratifs. Les pieces requises sont generalement : "
                "ADMINISTRATIVES : IFU, RCCM, attestation CNSS, quitus fiscal, attestation de "
                "non-faillite, agrement professionnel. "
                "TECHNIQUES : references similaires, CV personnel cle, moyens materiels, "
                "methodologie, planning. "
                "FINANCIERES : bordereau de prix, detail estimatif, garantie de soumission."
            ),
            "keywords": ["DAO", "dossier", "consultation", "cahier des charges", "bidding documents"],
            "country": None,
            "language": "fr",
        },
        {
            "category": "document_type",
            "subcategory": "PV_ATTRIBUTION",
            "title": "PV d'Attribution",
            "content": (
                "Le Proces-Verbal d'Attribution annonce le resultat d'un appel d'offres. "
                "Il indique le titulaire du marche, le montant attribue, le nombre d'offres "
                "recues, et parfois le classement des soumissionnaires. C'est une information "
                "strategique pour : comprendre les niveaux de prix du marche, identifier les "
                "concurrents, et ajuster sa strategie pour les prochains marches."
            ),
            "keywords": ["PV", "attribution", "titulaire", "resultat", "award notice"],
            "country": None,
            "language": "fr",
        },
        {
            "category": "document_type",
            "subcategory": "DECISION_ARMP",
            "title": "Decisions de l'ARMP",
            "content": (
                "L'Autorite de Regulation des Marches Publics (ARMP) du Benin rend des decisions "
                "sur les recours, autosaisines et sanctions. Types de decisions : "
                "- Recours : contestation d'une attribution ou d'un rejet, peut etre recevable "
                "ou irrecevable, fonde ou mal fonde. "
                "- Sanctions/Exclusions : exclusion d'entreprises pour fraude, falsification, "
                "collusion ou non-execution. Duree variable. "
                "- Autosaisines : l'ARMP detecte des irregularites et ordonne la correction. "
                "Ces decisions sont publiees sur armp.bj et constituent une jurisprudence "
                "essentielle pour les soumissionnaires."
            ),
            "keywords": ["ARMP", "decision", "recours", "sanction", "exclusion", "regulation"],
            "country": "Benin",
            "language": "fr",
        },

        # ── Procedures ──
        {
            "category": "procedure",
            "subcategory": "soumission",
            "title": "Procedure de soumission au Benin",
            "content": (
                "Pour soumissionner a un marche public au Benin : "
                "1. Retirer le DAO aupres de l'autorite contractante (souvent en ligne). "
                "2. Constituer le dossier administratif : IFU, RCCM, CNSS, quitus fiscal, "
                "attestation de non-faillite, agrement si requis. "
                "3. Preparer l'offre technique : references, CV, methodologie, planning, "
                "moyens materiels. "
                "4. Preparer l'offre financiere : remplir le BPU et le DE. "
                "5. Fournir la garantie de soumission (2% du montant pour travaux). "
                "6. Deposer l'offre AVANT la date limite (depot physique ou electronique). "
                "7. Assister a l'ouverture des plis (facultatif mais recommande). "
                "Le non-respect du format ou l'absence d'une piece = elimination."
            ),
            "keywords": ["soumission", "procedure", "depot", "offre", "Benin"],
            "country": "Benin",
            "language": "fr",
        },
        {
            "category": "procedure",
            "subcategory": "recours",
            "title": "Procedure de recours ARMP Benin",
            "content": (
                "Un soumissionnaire insatisfait peut saisir l'ARMP dans un delai de 10 jours "
                "ouvrables apres la notification de la decision contestee. "
                "Le recours doit etre motive et accompagne des pieces justificatives. "
                "L'ARMP examine le recours et rend une decision dans un delai de 7 jours. "
                "Effets : le recours peut etre suspensif (arrete la procedure en cours). "
                "La decision de l'ARMP est definitive en matiere administrative."
            ),
            "keywords": ["recours", "ARMP", "contestation", "delai", "suspension"],
            "country": "Benin",
            "language": "fr",
        },

        # ── Reglementation ──
        {
            "category": "reglementation",
            "subcategory": "code_marches",
            "title": "Code des marches publics du Benin",
            "content": (
                "Le Code des marches publics du Benin (Loi 2020-26 du 29 septembre 2020) "
                "regit toute la commande publique. Principes : liberte d'acces, egalite de "
                "traitement, transparence, bonne utilisation des deniers publics. "
                "Modes de passation : Appel d'offres ouvert (AOO), Appel d'offres restreint "
                "(AOR), Demande de renseignements et de prix (DRP), Gre a gre (exceptionnel). "
                "Seuils : les seuils determinent la procedure applicable selon le montant "
                "et le type de marche (travaux, fournitures, services)."
            ),
            "keywords": ["code", "loi", "reglementation", "seuil", "passation"],
            "country": "Benin",
            "language": "fr",
        },
        {
            "category": "reglementation",
            "subcategory": "uemoa",
            "title": "Directives UEMOA sur les marches publics",
            "content": (
                "Les directives UEMOA (2005) harmonisent les regles de passation des marches "
                "publics dans les 8 pays membres. Elles imposent : la mise en concurrence, "
                "la transparence, le controle des marches, la creation d'organes de regulation "
                "(ARMP dans chaque pays). Les marches finances par la Commission UEMOA "
                "suivent des procedures specifiques publiees sur le portail UEMOA."
            ),
            "keywords": ["UEMOA", "directive", "harmonisation", "regional"],
            "country": "UEMOA",
            "language": "fr",
        },

        # ── Sources d'information ──
        {
            "category": "source_info",
            "subcategory": "portails",
            "title": "Portails de marches publics du Benin",
            "content": (
                "Sources officielles a consulter quotidiennement : "
                "1. marches-publics.bj — Portail national, tous les AO et DAO. "
                "2. gouv.bj/opportunites/marches-publics — AO par ministere. "
                "3. armp.bj — Decisions, reglementation, appels d'offres. "
                "4. ADPME (epme.adpme.bj) — Opportunites pour PME. "
                "5. ABE (abe.bj) — Appels d'offres de l'Agence Beninoise de l'Environnement. "
                "6. jnmp.marches-publics.bj — Journal National des Marches Publics."
            ),
            "keywords": ["portail", "source", "site", "Benin", "officiel"],
            "country": "Benin",
            "language": "fr",
        },
        {
            "category": "source_info",
            "subcategory": "bailleurs",
            "title": "Sources bailleurs et organisations internationales",
            "content": (
                "Opportunites financees par bailleurs internationaux : "
                "- Banque mondiale : projects.worldbank.org (procurement) "
                "- BAD : afdb.org/fr/projects-and-operations/procurement "
                "- AFD : afd.fr (appels d'offres) "
                "- UNGM : ungm.org (systeme ONU) "
                "- UNFPA Benin : benin.unfpa.org/fr/call-for-submissions "
                "- Enabel : enabel.be/public-procurement "
                "- GIZ : giz.de/en/partner/contractor/tenders "
                "- TNS : buy.tns.org/public-opportunity-portal "
                "Ces sources publient des marches finances par des fonds internationaux "
                "et sont ouvertes aux entreprises des pays beneficiaires."
            ),
            "keywords": ["bailleur", "BM", "BAD", "AFD", "ONU", "UNGM", "international"],
            "country": None,
            "language": "fr",
        },

        # ── Lexique ──
        {
            "category": "lexique",
            "subcategory": "acronymes",
            "title": "Acronymes courants des marches publics",
            "content": (
                "AAO : Avis d'Appel d'Offres | "
                "AMI : Avis a Manifestation d'Interet | "
                "AOI : Appel d'Offres International | "
                "AON : Appel d'Offres National | "
                "AOO : Appel d'Offres Ouvert | "
                "AOR : Appel d'Offres Restreint | "
                "ARMP : Autorite de Regulation des Marches Publics | "
                "BPU : Bordereau de Prix Unitaires | "
                "CCT : Cahier des Charges Techniques | "
                "CNSS : Caisse Nationale de Securite Sociale | "
                "DAO : Dossier d'Appel d'Offres | "
                "DCE : Dossier de Consultation des Entreprises | "
                "DE : Detail Estimatif | "
                "DRP : Demande de Renseignements et de Prix | "
                "IFU : Identifiant Fiscal Unique | "
                "PPM : Plan de Passation des Marches | "
                "PRMP : Personne Responsable des Marches Publics | "
                "PV : Proces-Verbal | "
                "RCCM : Registre du Commerce et du Credit Mobilier | "
                "RFP : Request for Proposals | "
                "RFQ : Request for Quotation | "
                "TdR : Termes de Reference"
            ),
            "keywords": ["acronyme", "sigle", "abbreviation", "lexique"],
            "country": None,
            "language": "fr",
        },
    ]
