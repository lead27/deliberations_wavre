import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from extraire_deliberations import construire_url_base, detecter_seance_la_plus_recente


RACINE = Path(__file__).resolve().parent
GROUPES_COMMUNES = {
    "bw": [
        "wavre",
        "incourt",
        "ramillies",
        "rebecq",
        "tubize",
        "braine-le-chateau",
        "ittre",
        "braine-lalleud",
        "nivelles",
        "genappe",
        "lasne",
        "la-hulpe",
        "rixensart",
        "ottignies-louvain-la-neuve",
        "court-saint-etienne",
        "chastre",
        "mont-saint-guibert",
        "walhain",
        "chaumont-gistoux",
        "grez-doiceau",
        "helecine",
    ],
    "namur": [
        "dinant",
        "hamois",
        "havelange",
        "houyet",
        "onhaye",
        "rochefort",
        "somme-leuze",
        "vresse-sur-semois",
        "yvoir",
        "andenne",
        "assesse",
        "eghezee",
        "gembloux",
        "jemeppe-sur-sambre",
        "la-bruyere",
        "mettet",
        "namur",
        "ohey",
        "sambreville",
        "sombreffe",
        "cerfontaine",
        "doische",
        "florennes",
        "philippeville",
        "viroinval",
        "walcourt",
    ],
    "lux": [
        "arlon",
        "bastogne",
        "bertrix",
        "chiny",
        "daverdisse",
        "durbuy",
        "erezee",
        "etalle",
        "florenville",
        "habay",
        "la-roche-en-ardenne",
        "leglise",
        "libin",
        "libramont",
        "manhay",
        "marche-en-famenne",
        "martelange",
        "meix-devant-virton",
        "nassogne",
        "paliseul",
        "rendeux",
        "rouvroy",
        "sainte-ode",
        "saint-hubert",
        "saint-leger",
        "tellin",
        "virton",
        "wellin",
    ],
}
GROUPES_LABELS = {
    "bw": "Brabant wallon",
    "namur": "Namur",
    "lux": "Luxembourg",
}


def executer(description: str, commande: List[str]) -> None:
    """Exécute une commande externe avec affichage lisible."""
    print("=" * 80)
    print(description)
    print("=" * 80)
    try:
        resultat = subprocess.run(
            commande,
            cwd=RACINE,
            check=True,
        )
    except subprocess.CalledProcessError as err:
        raise RuntimeError(f"Échec de l'étape '{description}' (code {err.returncode})") from err
    print(f"✓ {description} terminée.\n")


def charger_derniere_seance(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Lit le fichier JSON des délibérations pour récupérer la séance enregistrée."""
    if not path.exists():
        return None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            donnees = json.load(handle)
    except (json.JSONDecodeError, OSError) as err:
        print(f"⚠ Impossible de lire {path}: {err}")
        return None, None

    if isinstance(donnees, dict):
        meta = donnees.get("seance") or {}
        return meta.get("id"), meta.get("nom")
    return None, None


def seance_identique(
    seance_a_id: Optional[str],
    seance_a_nom: Optional[str],
    seance_b_id: Optional[str],
    seance_b_nom: Optional[str],
) -> bool:
    """
    Compare deux séances par date et type/statut.

    Si la date du conseil est la même, on considère la séance inchangée et
    on court-circuite l'extraction/analyse, sauf si le type/statut a évolué
    (ex: "Projet de décision" -> "Décision").
    """
    date_a, type_a = decrire_seance(seance_a_id, seance_a_nom)
    date_b, type_b = decrire_seance(seance_b_id, seance_b_nom)

    if not date_a or not date_b or date_a != date_b:
        return False

    if type_a and type_b:
        return type_a == type_b

    # Si le type manque d'un côté, on s'arrête tout de même sur la date
    # pour éviter une réanalyse complète nocturne inutile.
    return True


def decrire_seance(seance_id: Optional[str], seance_nom: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extrait une clé de date et une clé de type à partir des métadonnées de séance."""
    date_nom, type_nom = extraire_date_et_type_depuis_nom(seance_nom)
    date_id = normaliser_date_depuis_id(seance_id)

    date_cle = date_nom or date_id
    type_cle = normaliser_type_seance(type_nom)
    return date_cle, type_cle


def extraire_date_et_type_depuis_nom(seance_nom: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not seance_nom:
        return None, None

    libelle = seance_nom.strip()
    if "—" in libelle:
        parts = [part.strip() for part in libelle.split("—", 1)]
    elif " - " in libelle:
        parts = [part.strip() for part in libelle.split(" - ", 1)]
    else:
        return normaliser_date_depuis_nom(libelle), None

    date_part = parts[0] if parts else libelle
    type_part = parts[1] if len(parts) > 1 else None
    return normaliser_date_depuis_nom(date_part), type_part


def normaliser_date_depuis_nom(date_texte: Optional[str]) -> Optional[str]:
    if not date_texte:
        return None

    # On compare la date telle qu'affichée, normalisée pour absorber les écarts
    # cosmétiques de casse ou d'espaces.
    normalisee = " ".join(date_texte.split()).casefold()
    return normalisee or None


def normaliser_date_depuis_id(seance_id: Optional[str]) -> Optional[str]:
    if not seance_id:
        return None

    # Les IDs ressemblent à 04-novembre-2025-20-00 ; on normalise seulement
    # la partie date/heure pour comparer les séances d'un même conseil.
    correspondance = re.match(r"^(\d{1,2}-[a-zA-ZÀ-ÿ]+-\d{4}-\d{1,2}-\d{2})", seance_id.strip())
    if correspondance:
        return correspondance.group(1).casefold()
    return seance_id.strip().casefold() or None


def normaliser_type_seance(type_texte: Optional[str]) -> Optional[str]:
    if not type_texte:
        return None
    return " ".join(type_texte.split()).casefold() or None


def fichier_deliberations_disponible(path: Path) -> bool:
    """Vérifie que le fichier de délibérations existe et contient des données."""
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            donnees = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return False

    if isinstance(donnees, dict):
        deliberations = donnees.get("deliberations") or []
    elif isinstance(donnees, list):
        deliberations = donnees
    else:
        return False
    return bool(deliberations)


def chemins_sortie(commune: str) -> Tuple[Path, Path, Path, Path]:
    """Construit les chemins de sortie standards pour une commune."""
    if commune == "wavre":
        return (
            RACINE / "deliberations_wavre.json",
            RACINE / "sujets_journalistiques.txt",
            RACINE / "sujets_journalistiques.json",
            RACINE / "sujets_journalistiques.html",
        )
    return (
        RACINE / f"deliberations_{commune}.json",
        RACINE / f"sujets_journalistiques_{commune}.txt",
        RACINE / f"sujets_journalistiques_{commune}.json",
        RACINE / f"sujets_journalistiques_{commune}.html",
    )


def parser_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chaîne d'automatisation quotidienne pour extraire, analyser et publier les délibérations.",
    )
    parser.add_argument("--skip-extraction", action="store_true", help="Ne pas relancer l'extraction des délibérations.")
    parser.add_argument("--skip-analyse", action="store_true", help="Ne pas relancer l'analyse journalistique.")
    parser.add_argument("--modele", default=None, help="Identifiant du modèle OpenAI transmis à analyser_sujets.py.")
    parser.add_argument(
        "--details",
        nargs="*",
        type=int,
        help="Numéros de délibérations à analyser en détail malgré le mode automatique.",
    )
    parser.add_argument("--skip-html", action="store_true", help="Ne pas générer la page HTML.")
    parser.add_argument("--skip-json", action="store_true", help="Ne pas générer le fichier JSON.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force l'extraction/analyse même si aucune nouvelle séance n'est détectée.",
    )
    parser.add_argument(
        "--communes",
        nargs="*",
        default=["wavre"],
        help="Liste des communes à analyser (ex: wavre incourt walhain).",
    )
    parser.add_argument(
        "--groupe",
        help="Nom d'un groupe de communes predefini (ex: bw).",
    )
    parser.add_argument(
        "--groupes",
        nargs="*",
        help="Liste de groupes de communes predefinis (ex: bw namur).",
    )
    return parser.parse_args()


def main() -> None:
    args = parser_arguments()

    groupes = []
    if args.groupes:
        groupes = [g.strip().lower() for g in args.groupes if g.strip()]
    elif args.groupe:
        groupes = [args.groupe.strip().lower()]

    if groupes:
        communes = []
        for groupe in groupes:
            communes_groupe = GROUPES_COMMUNES.get(groupe, [])
            if not communes_groupe:
                print(f"Groupe inconnu: {groupe}")
                return
            communes.extend(communes_groupe)
    else:
        communes = [commune.strip().lower() for commune in args.communes if commune.strip()]
    if not communes:
        print("Aucune commune fournie.")
        return

    for commune in communes:
        fichier_delib, fichier_texte, fichier_json, fichier_html = chemins_sortie(commune)
        url_base = construire_url_base(commune)

        if not args.skip_extraction:
            seance_vue_id, seance_vue_nom = charger_derniere_seance(fichier_delib)
            nouvelle_seance_id, nouvelle_seance_nom = detecter_seance_la_plus_recente(url_base)

            if (
                not args.force
                and seance_identique(seance_vue_id, seance_vue_nom, nouvelle_seance_id, nouvelle_seance_nom)
            ):
                print("=" * 80)
                print(f"Aucune nouvelle séance détectée pour {commune}, extraction ignorée.")
                print("=" * 80)
            else:
                if nouvelle_seance_id is None:
                    print(f"⚠ Séance la plus récente inconnue pour {commune}. L'extraction sera tout de même tentée.")

                executer(
                    f"Étape 1/2 - Extraction des délibérations ({commune})",
                    [sys.executable, "extraire_deliberations.py", "--commune", commune],
                )
        else:
            print(f"Extraction ignorée (--skip-extraction) pour {commune}.\n")

        seance_analyse_id, seance_analyse_nom = charger_derniere_seance(fichier_json)
        seance_delib_id, seance_delib_nom = charger_derniere_seance(fichier_delib)

        sorties_analyse_presentes = (
            fichier_json.exists()
            and fichier_texte.exists()
            and (args.skip_html or len(communes) > 1 or fichier_html.exists())
        )

        analyse_a_jour = sorties_analyse_presentes and seance_identique(
            seance_delib_id,
            seance_delib_nom,
            seance_analyse_id,
            seance_analyse_nom,
        )

        if not args.force and analyse_a_jour:
            print("=" * 80)
            print(f"Séance inchangée pour {commune}, analyse existante conservée.")
            print("=" * 80)
            continue

        if not args.force and not fichier_json.exists():
            print(f"Analyse absente pour {commune}, génération nécessaire.")
        elif not args.force and seance_delib_nom:
            print(f"Nouvelle séance à analyser pour {commune} : {seance_delib_nom}")

        if args.skip_analyse:
            print(f"Analyse journalistique ignorée (--skip-analyse) pour {commune}.\n")
            continue
        if not fichier_deliberations_disponible(fichier_delib):
            print(f"⚠ Aucune délibération disponible pour {commune}, analyse ignorée.")
            continue

        commande = [sys.executable, "analyser_sujets.py", "--auto", "--commune", commune]
        if args.modele:
            commande.extend(["--modele", args.modele])
        if args.skip_html or len(communes) > 1:
            commande.append("--skip-html")
        if args.skip_json:
            commande.append("--skip-json")
        if args.details:
            commande.append("--details")
            commande.extend(str(num) for num in args.details)

        executer(
            f"Étape 2/2 - Analyse journalistique et génération des sorties ({commune})",
            commande,
        )

    if len(communes) > 1 and not args.skip_html:
        commande_html = [sys.executable, "analyser_sujets.py", "--merge-html", "--communes", *communes]
        if groupes:
            group_labels = [GROUPES_LABELS.get(g, g.title()) for g in groupes]
            group_sizes = [len(GROUPES_COMMUNES[g]) for g in groupes]
            commande_html.extend(["--group-labels", *group_labels])
            commande_html.extend(["--group-sizes", *[str(n) for n in group_sizes]])
        executer("Compilation HTML multi-communes", commande_html)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"ERREUR : {exc}")
        sys.exit(1)
