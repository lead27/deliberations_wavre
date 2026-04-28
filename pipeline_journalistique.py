import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from extraire_deliberations import construire_url_base, detecter_seance_la_plus_recente


RACINE = Path(__file__).resolve().parent
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
        "anhee",
        "beauraing",
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


def charger_derniere_seance(path: Path) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Lit le fichier JSON des délibérations pour récupérer la séance enregistrée."""
    if not path.exists():
        return None, None, None
    try:
        with path.open("r", encoding="utf-8") as handle:
            donnees = json.load(handle)
    except (json.JSONDecodeError, OSError) as err:
        print(f"⚠ Impossible de lire {path}: {err}")
        return None, None, None

    if isinstance(donnees, dict):
        meta = donnees.get("seance") or {}
        nombre_points = meta.get("nombre_points")
        if nombre_points is None:
            if isinstance(donnees.get("deliberations"), list):
                nombre_points = len(donnees["deliberations"])
            elif isinstance(donnees.get("points"), list):
                nombre_points = len(donnees["points"])
        return meta.get("id"), meta.get("nom"), nombre_points
    return None, None, None


def seance_identique(
    seance_a_id: Optional[str],
    seance_a_nom: Optional[str],
    seance_a_nombre_points: Optional[int],
    seance_b_id: Optional[str],
    seance_b_nom: Optional[str],
    seance_b_nombre_points: Optional[int],
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
        if type_a != type_b:
            return False

    if (
        seance_a_nombre_points is not None
        and seance_b_nombre_points is not None
        and seance_a_nombre_points != seance_b_nombre_points
    ):
        return False

    # Si le type ou le nombre de points manque d'un côté, on s'arrête tout de
    # même sur la date pour éviter une réanalyse complète nocturne inutile.
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

    texte = " ".join(date_texte.split())
    if not texte:
        return None

    # Format le plus fréquent : "21 Avril 2026 (19:00)"
    correspondance = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})", texte)
    if correspondance:
        jour = int(correspondance.group(1))
        mois_label = enlever_accents(correspondance.group(2)).casefold()
        annee = int(correspondance.group(3))
        mois = MOIS_FR.get(mois_label)
        if mois:
            try:
                return date(annee, mois, jour).isoformat()
            except ValueError:
                pass

    # Fallback si on ne reconnaît pas le format : on garde une version
    # cosmétique stable plutôt que la chaîne brute.
    return texte.casefold()


def normaliser_date_depuis_id(seance_id: Optional[str]) -> Optional[str]:
    if not seance_id:
        return None

    identifiant = seance_id.strip()
    if not identifiant:
        return None

    # Certains IDs encodent la date sous la forme 04-novembre-2025-20-00.
    correspondance = re.match(r"^(\d{1,2})-([a-zA-ZÀ-ÿ]+)-(\d{4})(?:-\d{1,2}-\d{2})?", identifiant)
    if correspondance:
        jour = int(correspondance.group(1))
        mois_label = enlever_accents(correspondance.group(2)).casefold()
        annee = int(correspondance.group(3))
        mois = MOIS_FR.get(mois_label)
        if mois:
            try:
                return date(annee, mois, jour).isoformat()
            except ValueError:
                return None

    # D'autres IDs sont de simples UUID/hashs opaques : ils ne doivent pas
    # déclencher à eux seuls une réanalyse si le nom de séance suffit déjà.
    return None


def enlever_accents(texte: str) -> str:
    return "".join(
        caractere
        for caractere in unicodedata.normalize("NFD", texte)
        if unicodedata.category(caractere) != "Mn"
    )


def formater_resume_seance(
    seance_id: Optional[str],
    seance_nom: Optional[str],
    seance_nombre_points: Optional[int],
) -> str:
    date_cle, type_cle = decrire_seance(seance_id, seance_nom)
    return (
        f"id={seance_id or '-'} | nom={seance_nom or '-'} | "
        f"date_norm={date_cle or '-'} | type_norm={type_cle or '-'} | "
        f"points={seance_nombre_points if seance_nombre_points is not None else '-'}"
    )


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
            RACINE / "analyse_conseils_communaux.txt",
            RACINE / "analyse_conseils_communaux.json",
            RACINE / "analyse_conseils_communaux.html",
        )
    return (
        RACINE / f"deliberations_{commune}.json",
        RACINE / f"analyse_conseils_communaux_{commune}.txt",
        RACINE / f"analyse_conseils_communaux_{commune}.json",
        RACINE / f"analyse_conseils_communaux_{commune}.html",
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

    communes_en_echec: List[str] = []
    communes_traitees = 0

    for commune in communes:
        try:
            fichier_delib, fichier_texte, fichier_json, fichier_html = chemins_sortie(commune)
            url_base = construire_url_base(commune)

            if not args.skip_extraction:
                seance_vue_id, seance_vue_nom, seance_vue_nombre_points = charger_derniere_seance(fichier_delib)
                nouvelle_seance_id, nouvelle_seance_nom, nouvelle_seance_nombre_points = (
                    detecter_seance_la_plus_recente(url_base)
                )

                if (
                    not args.force
                    and seance_identique(
                        seance_vue_id,
                        seance_vue_nom,
                        seance_vue_nombre_points,
                        nouvelle_seance_id,
                        nouvelle_seance_nom,
                        nouvelle_seance_nombre_points,
                    )
                ):
                    print("=" * 80)
                    print(f"Aucune nouvelle séance détectée pour {commune}, extraction ignorée.")
                    print(
                        f"Séance connue    : "
                        f"{formater_resume_seance(seance_vue_id, seance_vue_nom, seance_vue_nombre_points)}"
                    )
                    print(
                        f"Séance détectée  : "
                        f"{formater_resume_seance(nouvelle_seance_id, nouvelle_seance_nom, nouvelle_seance_nombre_points)}"
                    )
                    print("=" * 80)
                else:
                    if nouvelle_seance_id is None and not args.force:
                        print("=" * 80)
                        print(
                            f"⚠ Séance la plus récente indétectable pour {commune}, "
                            "commune ignorée pour éviter d'extraire tout l'historique."
                        )
                        print("=" * 80)
                        continue
                    if nouvelle_seance_id is None:
                        print(
                            f"⚠ Séance la plus récente inconnue pour {commune}. "
                            "L'extraction complète est forcée (--force)."
                        )
                    elif not args.force:
                        print(f"Comparaison extraction {commune}:")
                        print(
                            f"  connue   -> "
                            f"{formater_resume_seance(seance_vue_id, seance_vue_nom, seance_vue_nombre_points)}"
                        )
                        print(
                            f"  détectée -> "
                            f"{formater_resume_seance(nouvelle_seance_id, nouvelle_seance_nom, nouvelle_seance_nombre_points)}"
                        )

                    executer(
                        f"Étape 1/2 - Extraction des délibérations ({commune})",
                        [sys.executable, "extraire_deliberations.py", "--commune", commune],
                    )
            else:
                print(f"Extraction ignorée (--skip-extraction) pour {commune}.\n")

            seance_analyse_id, seance_analyse_nom, seance_analyse_nombre_points = charger_derniere_seance(fichier_json)
            seance_delib_id, seance_delib_nom, seance_delib_nombre_points = charger_derniere_seance(fichier_delib)

            sorties_analyse_presentes = (
                fichier_json.exists()
                and fichier_texte.exists()
                and (args.skip_html or len(communes) > 1 or fichier_html.exists())
            )

            analyse_a_jour = sorties_analyse_presentes and seance_identique(
                seance_delib_id,
                seance_delib_nom,
                seance_delib_nombre_points,
                seance_analyse_id,
                seance_analyse_nom,
                seance_analyse_nombre_points,
            )

            if not args.force and analyse_a_jour:
                print("=" * 80)
                print(f"Séance inchangée pour {commune}, analyse existante conservée.")
                print(
                    f"Délibérations : "
                    f"{formater_resume_seance(seance_delib_id, seance_delib_nom, seance_delib_nombre_points)}"
                )
                print(
                    f"Analyse      : "
                    f"{formater_resume_seance(seance_analyse_id, seance_analyse_nom, seance_analyse_nombre_points)}"
                )
                print("=" * 80)
                communes_traitees += 1
                continue

            if not args.force and not fichier_json.exists():
                print(f"Analyse absente pour {commune}, génération nécessaire.")
            elif not args.force and seance_delib_nom:
                print(f"Nouvelle séance à analyser pour {commune} : {seance_delib_nom}")
                print(
                    f"Délibérations : "
                    f"{formater_resume_seance(seance_delib_id, seance_delib_nom, seance_delib_nombre_points)}"
                )
                print(
                    f"Analyse      : "
                    f"{formater_resume_seance(seance_analyse_id, seance_analyse_nom, seance_analyse_nombre_points)}"
                )

            if args.skip_analyse:
                print(f"Analyse journalistique ignorée (--skip-analyse) pour {commune}.\n")
                communes_traitees += 1
                continue
            if not fichier_deliberations_disponible(fichier_delib):
                print(f"⚠ Aucune délibération disponible pour {commune}, analyse ignorée.")
                communes_traitees += 1
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
            communes_traitees += 1
        except Exception as exc:
            print("=" * 80)
            print(f"⚠ Erreur pour {commune}: {exc}")
            print("La pipeline continue avec les autres communes.")
            print("=" * 80)
            communes_en_echec.append(commune)
            continue

    if len(communes) > 1 and not args.skip_html:
        commande_html = [sys.executable, "analyser_sujets.py", "--merge-html", "--communes", *communes]
        if groupes:
            group_labels = [GROUPES_LABELS.get(g, g.title()) for g in groupes]
            group_sizes = [len(GROUPES_COMMUNES[g]) for g in groupes]
            commande_html.extend(["--group-labels", *group_labels])
            commande_html.extend(["--group-sizes", *[str(n) for n in group_sizes]])
        executer("Compilation HTML multi-communes", commande_html)

    if communes_en_echec:
        print("=" * 80)
        print("Communes en échec ignorées pour cette exécution :")
        for commune in communes_en_echec:
            print(f"- {commune}")
        print("=" * 80)

    if communes_traitees == 0:
        raise RuntimeError("Aucune commune n'a pu être traitée avec succès.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"ERREUR : {exc}")
        sys.exit(1)
