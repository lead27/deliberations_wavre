import argparse
import io
import json
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

# Configuration
BASE_ROOT = "https://www.deliberations.be"
ANHEE_PROJETS_URL = (
    "https://www.anhee.be/ma-commune/vie-politique/conseil-communal/projets-de-deliberations"
)
BEAURAING_PROCES_VERBAUX_URL = (
    "https://www.beauraing.be/ma-commune/vie-politique/conseil-communal/proces-verbaux"
)
DELAI_ENTRE_REQUETES = 3  # secondes entre chaque requête pour éviter de surcharger le serveur
TIMEOUT_REQUETE = 30
NB_TENTATIVES = 3
MOIS_FR = {
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
}

COMMUNES_LABELS = {
    "andenne": "Andenne",
    "anhee": "Anhée",
    "arlon": "Arlon",
    "assesse": "Assesse",
    "bastogne": "Bastogne",
    "beauraing": "Beauraing",
    "bertrix": "Bertrix",
    "braine-lalleud": "Braine-l'Alleud",
    "braine-le-chateau": "Braine-le-Château",
    "cerfontaine": "Cerfontaine",
    "chastre": "Chastre",
    "chaumont-gistoux": "Chaumont-Gistoux",
    "chiny": "Chiny",
    "court-saint-etienne": "Court-Saint-Etienne",
    "daverdisse": "Daverdisse",
    "dinant": "Dinant",
    "doische": "Doische",
    "durbuy": "Durbuy",
    "eghezee": "Eghezée",
    "erezee": "Erezée",
    "etalle": "Etalle",
    "florennes": "Florennes",
    "florenville": "Florenville",
    "gembloux": "Gembloux",
    "genappe": "Genappe",
    "grez-doiceau": "Grez-Doiceau",
    "habay": "Habay",
    "hamois": "Hamois",
    "havelange": "Havelange",
    "helecine": "Hélécine",
    "houyet": "Houyet",
    "incourt": "Incourt",
    "ittre": "Ittre",
    "jemeppe-sur-sambre": "Jemeppe-sur-Sambre",
    "la-bruyere": "La Bruyère",
    "la-hulpe": "La Hulpe",
    "la-roche-en-ardenne": "La Roche-en-Ardenne",
    "lasne": "Lasne",
    "leglise": "Léglise",
    "libin": "Libin",
    "libramont": "Libramont-Chevigny",
    "manhay": "Manhay",
    "marche-en-famenne": "Marche-en-Famenne",
    "martelange": "Martelange",
    "meix-devant-virton": "Meix-devant-Virton",
    "mettet": "Mettet",
    "mont-saint-guibert": "Mont-Saint-Guibert",
    "namur": "Namur",
    "nassogne": "Nassogne",
    "nivelles": "Nivelles",
    "ohey": "Ohey",
    "onhaye": "Onhaye",
    "ottignies-louvain-la-neuve": "Ottignies-Louvain-la-Neuve",
    "paliseul": "Paliseul",
    "philippeville": "Philippeville",
    "ramillies": "Ramillies",
    "rebecq": "Rebecq",
    "rendeux": "Rendeux",
    "rixensart": "Rixensart",
    "rochefort": "Rochefort",
    "rouvroy": "Rouvroy",
    "saint-hubert": "Saint-Hubert",
    "saint-leger": "Saint-Léger",
    "sainte-ode": "Sainte-Ode",
    "sambreville": "Sambreville",
    "sombreffe": "Sombreffe",
    "somme-leuze": "Somme-Leuze",
    "tellin": "Tellin",
    "tubize": "Tubize",
    "viroinval": "Viroinval",
    "virton": "Virton",
    "vresse-sur-semois": "Vresse-sur-Semois",
    "walcourt": "Walcourt",
    "walhain": "Walhain",
    "wavre": "Wavre",
    "wellin": "Wellin",
    "yvoir": "Yvoir",
}


def _nom_commune_affichage(commune: str) -> str:
    slug = commune.strip().lower().replace("_", "-")
    return COMMUNES_LABELS.get(slug, slug.replace("-", " ").title())


def _titre_secours_depuis_url(url: str) -> str:
    slug = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    correspondances = {
        "convention-relative-a-ladhesion-au-package-de-base-ecosysteme-dinbw-en-matiere-denergie-approbation-svm": (
            "Convention relative à l'adhésion au package de base "
            "Écosystème d'in BW en matière d'énergie - Approbation / SVM"
        ),
        "bpost-projet-bbox-distributeur-de-colis-sur-le-territoire-communal-convention-de-mise-a-disposition": (
            "bpost - Projet bBox distributeur de colis sur le territoire communal "
            "- Convention de mise à disposition"
        ),
        "pcdr-operation-de-developpement-rural-rapport-annuel-2025-approbation": (
            "PCDR - Opération de développement rural - Rapport annuel 2025 - Approbation"
        ),
    }
    if slug in correspondances:
        return correspondances[slug]

    titre = slug.replace("-", " ").strip()
    return titre[:1].upper() + titre[1:] if titre else "Titre indisponible"


def _get_with_retries(url: str, timeout: int = TIMEOUT_REQUETE, retries: int = NB_TENTATIVES):
    derniere_erreur = None
    for tentative in range(1, retries + 1):
        try:
            return requests.get(url, timeout=timeout)
        except requests.RequestException as err:
            derniere_erreur = err
            print(f"  ⚠ Tentative {tentative}/{retries} échouée pour {url}: {err}")
            time.sleep(2)
    raise derniere_erreur

def construire_url_base(commune: str, base_root: str = BASE_ROOT) -> str:
    if commune == "anhee":
        return ANHEE_PROJETS_URL
    if commune == "beauraing":
        return f"{BEAURAING_PROCES_VERBAUX_URL}/conseils-communaux-{datetime.now().year}"
    return f"{base_root.rstrip('/')}/{commune}/decisions"


def _mois_fr(numero: int) -> str:
    labels = {
        1: "Janvier",
        2: "Février",
        3: "Mars",
        4: "Avril",
        5: "Mai",
        6: "Juin",
        7: "Juillet",
        8: "Août",
        9: "Septembre",
        10: "Octobre",
        11: "Novembre",
        12: "Décembre",
    }
    return labels.get(numero, str(numero))


def _anhee_lister_pdfs(url_base: str) -> List[str]:
    response = _get_with_retries(url_base)
    soup = BeautifulSoup(response.content, "html.parser")
    pdfs = []
    vus = set()
    for lien in soup.find_all("a", href=True):
        href = lien["href"].strip()
        if ".pdf" not in href.lower():
            continue
        url = urljoin(url_base, href)
        if "/view" in url:
            url = url.split("/view", 1)[0]
        if url not in vus:
            vus.add(url)
            pdfs.append(url)
    return pdfs


def _anhee_extraire_datetime_depuis_url(url: str) -> Optional[datetime]:
    correspondance = re.search(r"(\d{2})-(\d{2})-(\d{4})", url)
    if correspondance:
        jour = int(correspondance.group(1))
        mois = int(correspondance.group(2))
        annee = int(correspondance.group(3))
    else:
        correspondance = re.search(r"(\d{1,2})-([a-zA-ZÀ-ÿ]+)-(\d{4})", url)
        if not correspondance:
            return None
        jour = int(correspondance.group(1))
        mois_label = correspondance.group(2).strip().lower()
        mois = MOIS_FR.get(mois_label)
        annee = int(correspondance.group(3))
        if mois is None:
            return None
    try:
        return datetime(annee, mois, jour, 20, 0)
    except ValueError:
        return None


def _anhee_detecter_pdf_le_plus_recent(url_base: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    print("Détection de la séance la plus récente (source PDF Anhée)...")
    pdfs = _anhee_lister_pdfs(url_base)
    candidats = []
    for pdf_url in pdfs:
        date_pdf = _anhee_extraire_datetime_depuis_url(pdf_url)
        if date_pdf is None:
            continue
        candidats.append((date_pdf, pdf_url))

    if not candidats:
        print("Impossible de détecter un PDF de séance récent.\n")
        return None, None, None

    date_seance, pdf_url = max(candidats, key=lambda item: item[0])
    seance_nom = f"{date_seance.day:02d} {_mois_fr(date_seance.month)} {date_seance.year} (20:00) — Projet de décision"
    seance_id = f"{date_seance:%d-%m-%Y}-20-00-projet-de-decision"
    print(f"Séance détectée : {seance_nom}")
    print(f"PDF : {pdf_url}\n")
    return seance_id, seance_nom, pdf_url


def _anhee_telecharger_pdf(pdf_url: str) -> PdfReader:
    response = _get_with_retries(pdf_url, timeout=60)
    return PdfReader(io.BytesIO(response.content))


def _anhee_normaliser_ligne_pdf(ligne: str) -> str:
    return " ".join((ligne or "").replace("\xa0", " ").split()).strip()


def _anhee_ratio_majuscules(texte: str) -> float:
    lettres = [caractere for caractere in texte if caractere.isalpha()]
    if not lettres:
        return 0.0
    return sum(1 for caractere in lettres if caractere.isupper()) / len(lettres)


def _anhee_est_titre_point(texte: str) -> bool:
    texte = texte.strip()
    if not texte:
        return False
    if len(texte) < 12:
        return False
    if _anhee_ratio_majuscules(texte) < 0.8:
        return False
    return True


def _anhee_est_suite_titre(texte: str) -> bool:
    texte = texte.strip()
    if not texte:
        return False
    if texte.startswith(("Vu ", "Attendu", "Considérant", "DECIDE", "DÉCIDE", "Sur proposition")):
        return False
    return _anhee_est_titre_point(texte)


def _anhee_extraire_points_depuis_pdf(pdf_url: str) -> List[dict]:
    print(f"📄 Extraction du PDF Anhée : {pdf_url.split('/')[-1][:60]}...")
    time.sleep(DELAI_ENTRE_REQUETES)
    lecteur = _anhee_telecharger_pdf(pdf_url)
    points = []
    point_courant = None
    motif_point = re.compile(r"^(\d+)\.\s+(.+)$")

    for numero_page, page in enumerate(lecteur.pages, start=1):
        texte = page.extract_text() or ""
        for brute in texte.splitlines():
            ligne = _anhee_normaliser_ligne_pdf(brute)
            if not ligne:
                continue
            if re.fullmatch(r"\d+\s*/\s*\d+", ligne):
                continue

            correspondance = motif_point.match(ligne)
            if correspondance:
                titre = correspondance.group(2).strip()
                if point_courant and not _anhee_est_titre_point(titre):
                    point_courant["contenu"] += "\n" + ligne
                    continue

                if point_courant:
                    points.append(point_courant)
                url_point = f"{pdf_url}#page={numero_page}&search={quote(titre[:120])}"
                point_courant = {
                    "url": url_point,
                    "titre": titre,
                    "contenu": titre,
                }
                continue

            if point_courant:
                if point_courant["contenu"] == point_courant["titre"] and _anhee_est_suite_titre(ligne):
                    point_courant["titre"] = f"{point_courant['titre']} {ligne}".strip()
                    point_courant["contenu"] = point_courant["titre"]
                    point_courant["url"] = f"{pdf_url}#page={numero_page}&search={quote(point_courant['titre'][:120])}"
                    continue
                point_courant["contenu"] += "\n" + ligne

    if point_courant:
        points.append(point_courant)

    print(f"✅ Extraction PDF réussie : {len(points)} point(s)\n")
    return points


def _beauraing_url_annee(annee: int) -> str:
    return f"{BEAURAING_PROCES_VERBAUX_URL}/conseils-communaux-{annee}"


def _beauraing_lister_pdfs(url_base: str) -> List[str]:
    response = _get_with_retries(url_base)
    soup = BeautifulSoup(response.content, "html.parser")
    pdfs = []
    vus = set()
    for lien in soup.find_all("a", href=True):
        href = lien.get("href", "").strip()
        if ".pdf" not in href.lower():
            continue
        url = urljoin(url_base, href)
        if "/view" in url:
            url = url.split("/view", 1)[0]
        if url not in vus:
            vus.add(url)
            pdfs.append(url)
    return pdfs


def _beauraing_extraire_datetime_depuis_url(url: str) -> Optional[datetime]:
    correspondance = re.search(r"cc-(\d{2})-(\d{2})-(\d{2,4})", url.lower())
    if not correspondance:
        return None
    jour = int(correspondance.group(1))
    mois = int(correspondance.group(2))
    annee = int(correspondance.group(3))
    if annee < 100:
        annee += 2000
    try:
        return datetime(annee, mois, jour, 20, 0)
    except ValueError:
        return None


def _beauraing_detecter_pdf_le_plus_recent(url_base: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    print("Détection de la séance la plus récente (source PDF Beauraing)...")
    pages_a_verifier = [url_base]
    annee_courante = datetime.now().year
    if not url_base.endswith(str(annee_courante - 1)):
        pages_a_verifier.append(_beauraing_url_annee(annee_courante - 1))

    candidats = []
    for page_url in pages_a_verifier:
        try:
            pdfs = _beauraing_lister_pdfs(page_url)
        except Exception:
            continue
        for pdf_url in pdfs:
            if "projets-de-deliberations" not in pdf_url.lower():
                continue
            date_pdf = _beauraing_extraire_datetime_depuis_url(pdf_url)
            if date_pdf is None:
                continue
            candidats.append((date_pdf, pdf_url))

    if not candidats:
        print("Impossible de détecter un PDF de projets récent.\n")
        return None, None, None

    date_seance, pdf_url = max(candidats, key=lambda item: item[0])
    seance_nom = f"{date_seance.day:02d} {_mois_fr(date_seance.month)} {date_seance.year} (20:00) — Projet de décision"
    seance_id = f"{date_seance:%d-%m-%Y}-20-00-projet-de-decision"
    print(f"Séance détectée : {seance_nom}")
    print(f"PDF : {pdf_url}\n")
    return seance_id, seance_nom, pdf_url


def _beauraing_telecharger_pdf(pdf_url: str) -> PdfReader:
    response = _get_with_retries(pdf_url, timeout=60)
    return PdfReader(io.BytesIO(response.content))


def _beauraing_normaliser_ligne_pdf(ligne: str) -> str:
    return " ".join((ligne or "").replace("\xa0", " ").split()).strip()


def _beauraing_extraire_odj_premiere_page(lecteur: PdfReader) -> List[Tuple[int, str]]:
    texte = lecteur.pages[0].extract_text() or ""
    lignes = [_beauraing_normaliser_ligne_pdf(ligne) for ligne in texte.splitlines()]
    lignes = [ligne for ligne in lignes if ligne]

    try:
        index_ordre = lignes.index("Ordre du jour")
    except ValueError:
        return []

    items = []
    point_courant = None
    compteur_seance_publique = 0
    motif_point = re.compile(r"^(\d+)\.\s+(.+)$")

    for ligne in lignes[index_ordre + 1:]:
        if ligne == "I. Séance publique":
            compteur_seance_publique += 1
            if compteur_seance_publique >= 2:
                break
            continue
        if compteur_seance_publique == 0:
            continue
        if ligne.startswith("II."):
            break

        correspondance = motif_point.match(ligne)
        if correspondance:
            if point_courant is not None:
                items.append(point_courant)
            point_courant = [int(correspondance.group(1)), correspondance.group(2).strip()]
        elif point_courant is not None:
            point_courant[1] = f"{point_courant[1]} {ligne}".strip()

    if point_courant is not None:
        items.append(point_courant)

    return [(numero, titre) for numero, titre in items]


def _beauraing_est_ligne_contenu(texte: str) -> bool:
    prefixes = (
        "Vu ",
        "Attendu",
        "Considérant",
        "Aucun avis",
        "Ouï ",
        "Sur proposition",
        "PROPOSITION",
        "Article",
        "Art.",
        "Néant",
    )
    return texte.startswith(prefixes)


def _beauraing_extraire_points_depuis_pdf(pdf_url: str) -> List[dict]:
    print(f"📄 Extraction du PDF Beauraing : {pdf_url.split('/')[-1][:60]}...")
    time.sleep(DELAI_ENTRE_REQUETES)
    lecteur = _beauraing_telecharger_pdf(pdf_url)
    ordre_du_jour = _beauraing_extraire_odj_premiere_page(lecteur)
    if not ordre_du_jour:
        print("⚠ Impossible d'extraire l'ordre du jour depuis la première page.\n")
        return []

    titres_par_numero = {numero: titre for numero, titre in ordre_du_jour}
    points = []
    point_courant = None
    motif_point = re.compile(r"^(\d+)\.\s+(.+)$")
    dans_details = False
    compteur_seance_publique = 0

    for numero_page, page in enumerate(lecteur.pages, start=1):
        texte = page.extract_text() or ""
        for brute in texte.splitlines():
            ligne = _beauraing_normaliser_ligne_pdf(brute)
            if not ligne:
                continue
            if re.fullmatch(r"\d+\s*/\s*\d+", ligne):
                continue

            if not dans_details:
                if ligne == "I. Séance publique":
                    compteur_seance_publique += 1
                    if compteur_seance_publique >= 2:
                        dans_details = True
                continue

            correspondance = motif_point.match(ligne)
            if correspondance:
                numero_point = int(correspondance.group(1))
                if numero_point not in titres_par_numero:
                    if point_courant is not None:
                        point_courant["contenu"] += "\n" + ligne
                    continue

                if point_courant:
                    points.append(point_courant)
                titre = titres_par_numero[numero_point]
                point_courant = {
                    "url": f"{pdf_url}#page={numero_page}&search={quote(titre[:120])}",
                    "titre": titre,
                    "contenu": "",
                }
                continue

            if point_courant is None:
                continue

            if not point_courant["contenu"] and not _beauraing_est_ligne_contenu(ligne):
                continue

            if point_courant["contenu"]:
                point_courant["contenu"] += "\n" + ligne
            else:
                point_courant["contenu"] = ligne

    if point_courant:
        points.append(point_courant)

    for point in points:
        if not point["contenu"]:
            point["contenu"] = point["titre"]

    print(f"✅ Extraction PDF réussie : {len(points)} point(s)\n")
    return points


def _extraire_nombre_points_depuis_libelle_seance(libelle: Optional[str]) -> Optional[int]:
    if not libelle:
        return None
    correspondance = re.search(r"\((\d+)(?:\s+points?)?\)\s*$", libelle.strip(), re.IGNORECASE)
    if correspondance:
        return int(correspondance.group(1))
    return None


def detecter_seance_la_plus_recente(url_base: str):
    """
    Cette fonction détecte automatiquement la séance la plus récente
    en analysant la première page des décisions
    """
    if "anhee.be" in url_base:
        seance_id, seance_nom, _ = _anhee_detecter_pdf_le_plus_recent(url_base)
        return seance_id, seance_nom, None
    if "beauraing.be" in url_base:
        seance_id, seance_nom, _ = _beauraing_detecter_pdf_le_plus_recent(url_base)
        return seance_id, seance_nom, None

    print("Détection de la séance la plus récente...")
    
    response = _get_with_retries(url_base)
    soup = BeautifulSoup(response.content, 'html.parser')
    
    # Chercher le sélecteur de séance
    select_seance = soup.find('select', {'id': 'seance'})

    if select_seance:
        # Trouver l'option sélectionnée (selected="selected")
        option_selectionnee = select_seance.find('option', {'selected': 'selected'})
        if option_selectionnee is None:
            # Fallback : première option avec une valeur
            option_selectionnee = select_seance.find('option', {'value': True})
        if option_selectionnee:
            seance_id = option_selectionnee.get('value')
            seance_nom = option_selectionnee.get('title') or option_selectionnee.get_text(strip=True)
            nombre_points = _extraire_nombre_points_depuis_libelle_seance(option_selectionnee.get_text(" ", strip=True))
            print(f"Séance détectée : {seance_nom}")
            if nombre_points is not None:
                print(f"Points détectés dans le sélecteur : {nombre_points}")
            print(f"ID : {seance_id}\n")
            return seance_id, seance_nom, nombre_points

    print("Impossible de détecter la séance. Utilisation de la liste par défaut...\n")
    return None, None, None

def extraire_liens_deliberations(seance_id, url_base: str):
    """
    Étape 1 : Cette fonction va parcourir TOUTES les pages
    d'une séance spécifique et récupère tous les liens
    """
    print("📥 Analyse de la pagination...")
    
    tous_les_liens = []
    liens_vus = set()  # Pour éviter les doublons
    page_actuelle = 0
    pages_vides_consecutives = 0
    
    use_faceted = False

    increment = 20

    while True:
        # Construction de l'URL avec la séance et la pagination
        if use_faceted:
            base_faceted = f"{url_base.rstrip('/')}/@@faceted_query"
            if page_actuelle == 0:
                url = base_faceted
            else:
                url = f"{base_faceted}?b_start={page_actuelle}"
            if seance_id:
                separateur = "&" if "?" in url else "?"
                url = f"{url}{separateur}seance={seance_id}"
        elif seance_id:
            if page_actuelle == 0:
                url = f"{url_base}?seance={seance_id}"
            else:
                url = f"{url_base}?seance={seance_id}&b_start:int={page_actuelle}"
        else:
            if page_actuelle == 0:
                url = f"{url_base}"
            else:
                url = f"{url_base}?b_start:int={page_actuelle}"
        
        print(f"  📄 Page {page_actuelle // 20 + 1}...")
        
        try:
            # On fait une demande pour récupérer la page web
            response = _get_with_retries(url)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # On cherche toutes les cartes de délibérations
            cartes = soup.find_all('div', class_='item-card')
            
            if not cartes:
                if not use_faceted:
                    # Certaines communes chargent les résultats via @@faceted_query
                    print("  ⚠️  Aucune carte trouvée, tentative via @@faceted_query...")
                    use_faceted = True
                    page_actuelle = 0
                    pages_vides_consecutives = 0
                    continue
                # Plus de cartes = on a fini
                print("  ⚠️  Aucune carte trouvée sur cette page")
                break
            
            nouveaux_liens = 0
            for carte in cartes:
                # Dans chaque carte, on cherche le lien
                lien_element = carte.find('a', class_='filled-link')
                if lien_element and lien_element.get('href'):
                    # On récupère l'URL complète
                    url_complete = lien_element['href']
                    if not url_complete.startswith('http'):
                        url_complete = 'https://www.deliberations.be' + url_complete
                    
                    # On vérifie qu'on n'a pas déjà ce lien
                    if url_complete not in liens_vus:
                        tous_les_liens.append(url_complete)
                        liens_vus.add(url_complete)
                        nouveaux_liens += 1
            
            print(f"     → {nouveaux_liens} nouvelles délibérations trouvées")
            if use_faceted:
                increment = max(len(cartes), 1)
            
            # Si on n'a trouvé aucun nouveau lien, on arrête
            if nouveaux_liens == 0:
                pages_vides_consecutives += 1
                if pages_vides_consecutives >= 2:
                    print("  ✓ Plus de nouvelles délibérations")
                    break
            else:
                pages_vides_consecutives = 0
            
            # Passer à la page suivante
            page_actuelle += increment
            time.sleep(1)  # Petite pause entre les pages
            
        except Exception as e:
            print(f"  ❌ Erreur sur cette page: {e}")
            break
    
    print(f"\n✅ Trouvé {len(tous_les_liens)} délibérations au total\n")
    return tous_les_liens

def extraire_contenu_deliberation(url):
    """
    Étape 2 : Cette fonction va chercher le contenu détaillé
    d'une délibération spécifique
    """
    print(f"📄 Extraction de : {url.split('/')[-1][:50]}...")
    
    # On attend un peu pour ne pas surcharger le serveur
    time.sleep(DELAI_ENTRE_REQUETES)
    
    try:
        response = _get_with_retries(url)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # On récupère le titre
        titre = soup.find('h1')
        titre_texte = titre.get_text(strip=True) if titre else "Titre non trouvé"
        if "bad gateway" in titre_texte.casefold():
            titre_texte = _titre_secours_depuis_url(url)
        
        # On récupère tout le contenu principal
        contenu = soup.find('article', id='content')
        if contenu:
            # On enlève les balises HTML pour garder juste le texte
            texte = contenu.get_text(separator='\n', strip=True)
        else:
            texte = "Contenu indisponible lors de l'extraction."

        print(f"✅ Extraction réussie\n")
        
        return {
            'url': url,
            'titre': titre_texte,
            'contenu': texte
        }
    
    except Exception as e:
        print(f"❌ Erreur : {e}\n")
        return {
            'url': url,
            'titre': 'Erreur',
            'contenu': f'Impossible d\'extraire le contenu : {e}'
        }

def sauvegarder_resultats(
    deliberations,
    seance_id=None,
    seance_nom=None,
    seance_nombre_points=None,
    nom_fichier="deliberations_wavre.json",
    commune_slug="wavre",
    commune_nom=None,
):
    """
    Étape 3 : Cette fonction sauvegarde tous les résultats
    dans un fichier JSON (format facile à lire) avec des métadonnées
    """
    print(f"💾 Sauvegarde des résultats dans {nom_fichier}...")

    export = {
        "exported_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "seance": {
            "id": seance_id,
            "nom": seance_nom,
            "nombre_points": seance_nombre_points,
        },
        "commune": {
            "slug": commune_slug,
            "nom": commune_nom,
        },
        "deliberations": deliberations,
    }

    with open(nom_fichier, 'w', encoding='utf-8') as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    print(f"✅ Sauvegarde terminée !\n")

def creer_resume_texte(deliberations, nom_fichier, seance_nom, commune_nom):
    """
    Étape 4 : Cette fonction crée un fichier texte facile à lire
    avec toutes les délibérations
    """
    print(f"📝 Création du résumé en texte dans {nom_fichier}...")
    
    with open(nom_fichier, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write(f"DÉLIBÉRATIONS DU CONSEIL COMMUNAL DE {commune_nom.upper()}\n")
        if seance_nom:
            f.write(f"{seance_nom}\n")
        f.write("=" * 80 + "\n\n")
        
        for i, delib in enumerate(deliberations, 1):
            f.write(f"\n{'='*80}\n")
            f.write(f"POINT {i}\n")
            f.write(f"{'='*80}\n\n")
            f.write(f"TITRE : {delib['titre']}\n\n")
            f.write(f"URL : {delib['url']}\n\n")
            f.write(f"CONTENU :\n{'-'*80}\n")
            f.write(delib['contenu'])
            f.write(f"\n\n")
    
    print(f"✅ Résumé créé !\n")

# ===== PROGRAMME PRINCIPAL =====
def parser_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extraction des délibérations communales.")
    parser.add_argument("--commune", default="wavre", help="Slug de la commune (ex: wavre, incourt).")
    parser.add_argument(
        "--base-root",
        default=BASE_ROOT,
        help="Domaine racine des délibérations (ex: https://www.deliberations.be).",
    )
    parser.add_argument("--output-json", default=None, help="Chemin du fichier JSON de sortie.")
    parser.add_argument("--output-text", default=None, help="Chemin du fichier texte de sortie.")
    return parser.parse_args()


def main():
    """
    C'est ici que tout commence !
    """
    args = parser_arguments()
    commune_slug = args.commune.strip().lower()
    commune_nom = _nom_commune_affichage(commune_slug)
    url_base = construire_url_base(commune_slug, args.base_root)

    if args.output_json:
        fichier_json = args.output_json
    elif commune_slug == "wavre":
        fichier_json = "deliberations_wavre.json"
    else:
        fichier_json = f"deliberations_{commune_slug}.json"

    if args.output_text:
        fichier_texte = args.output_text
    elif commune_slug == "wavre":
        fichier_texte = "resume_deliberations.txt"
    else:
        fichier_texte = f"resume_deliberations_{commune_slug}.txt"

    print("\n" + "="*80)
    print(f"EXTRACTION DES DÉLIBÉRATIONS DU CONSEIL COMMUNAL DE {commune_nom.upper()}")
    print("="*80 + "\n")
    
    if commune_slug == "anhee":
        seance_id, seance_nom, pdf_url = _anhee_detecter_pdf_le_plus_recent(url_base)
        seance_nombre_points = None
    elif commune_slug == "beauraing":
        seance_id, seance_nom, pdf_url = _beauraing_detecter_pdf_le_plus_recent(url_base)
        seance_nombre_points = None
    else:
        pdf_url = None
        seance_id, seance_nom, seance_nombre_points = detecter_seance_la_plus_recente(url_base)
    
    if not seance_id:
        print(
            "⚠ Séance non détectée. Extraction annulée pour éviter de parcourir "
            "toute la liste historique de la commune."
        )
        print("   Utilisez --force via le pipeline si vous voulez vraiment tenter une extraction complète.")
        return
    
    if commune_slug == "anhee":
        if not pdf_url:
            print("Aucun PDF de séance détecté. Vérifiez la page source.")
            return
        deliberations = _anhee_extraire_points_depuis_pdf(pdf_url)
        if not deliberations:
            print("Aucun point n'a pu être extrait du PDF.")
            return
        seance_nombre_points = len(deliberations)
    elif commune_slug == "beauraing":
        if not pdf_url:
            print("Aucun PDF de séance détecté. Vérifiez la page source.")
            return
        deliberations = _beauraing_extraire_points_depuis_pdf(pdf_url)
        if not deliberations:
            print("Aucun point n'a pu être extrait du PDF.")
            return
        seance_nombre_points = len(deliberations)
    else:
        # Étape 1 : Récupérer tous les liens de cette séance
        liens = extraire_liens_deliberations(seance_id, url_base)
        
        if not liens:
            print("Aucune délibération trouvée. Vérifiez l'URL.")
            return
        
        # Étape 2 : Extraire le contenu de chaque délibération
        print(f"Début de l'extraction de {len(liens)} délibérations...")
        print(f"Temps estimé : environ {len(liens) * DELAI_ENTRE_REQUETES // 60} minutes\n")
        
        deliberations = []
        for i, lien in enumerate(liens, 1):
            print(f"[{i}/{len(liens)}]")
            delib = extraire_contenu_deliberation(lien)
            deliberations.append(delib)
        if seance_nombre_points is None:
            seance_nombre_points = len(deliberations)
    
    # Étape 3 : Sauvegarder les résultats
    sauvegarder_resultats(
        deliberations,
        seance_id,
        seance_nom,
        seance_nombre_points=seance_nombre_points,
        nom_fichier=fichier_json,
        commune_slug=commune_slug,
        commune_nom=commune_nom,
    )
    creer_resume_texte(deliberations, fichier_texte, seance_nom, commune_nom)
    
    print("\n" + "="*80)
    print("EXTRACTION TERMINÉE !")
    print("="*80)
    print(f"\nVous pouvez consulter :")
    print(f"  - {fichier_json} (format structuré)")
    print(f"  - {fichier_texte} (format texte lisible)")
    print("\nCes fichiers sont dans le même dossier que votre script.\n")

# Point d'entrée du programme
if __name__ == "__main__":
    main()
