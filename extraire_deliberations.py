import argparse
import json
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

# Configuration
BASE_ROOT = "https://www.deliberations.be"
DELAI_ENTRE_REQUETES = 3  # secondes entre chaque requête pour éviter de surcharger le serveur
TIMEOUT_REQUETE = 30
NB_TENTATIVES = 3


def _nom_commune_affichage(commune: str) -> str:
    return commune.replace("-", " ").title()


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
    return f"{base_root.rstrip('/')}/{commune}/decisions"


def detecter_seance_la_plus_recente(url_base: str):
    """
    Cette fonction détecte automatiquement la séance la plus récente
    en analysant la première page des décisions
    """
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
            print(f"Séance détectée : {seance_nom}")
            print(f"ID : {seance_id}\n")
            return seance_id, seance_nom

    print("Impossible de détecter la séance. Utilisation de la liste par défaut...\n")
    return None, None

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
        
        # On récupère tout le contenu principal
        contenu = soup.find('article', id='content')
        if contenu:
            # On enlève les balises HTML pour garder juste le texte
            texte = contenu.get_text(separator='\n', strip=True)
        else:
            texte = "Contenu non trouvé"
        
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
            "nom": seance_nom
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

def creer_resume_texte(deliberations, nom_fichier, seance_nom):
    """
    Étape 4 : Cette fonction crée un fichier texte facile à lire
    avec toutes les délibérations
    """
    print(f"📝 Création du résumé en texte dans {nom_fichier}...")
    
    with open(nom_fichier, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("DÉLIBÉRATIONS DU CONSEIL COMMUNAL DE WAVRE\n")
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
    
    # Étape 0 : Détecter la séance la plus récente
    seance_id, seance_nom = detecter_seance_la_plus_recente(url_base)
    
    if not seance_id:
        print("⚠ Séance non détectée, tentative d'extraction sur la liste par défaut.")
    
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
    
    # Étape 3 : Sauvegarder les résultats
    sauvegarder_resultats(
        deliberations,
        seance_id,
        seance_nom,
        nom_fichier=fichier_json,
        commune_slug=commune_slug,
        commune_nom=commune_nom,
    )
    creer_resume_texte(deliberations, fichier_texte, seance_nom)
    
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
