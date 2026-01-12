import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from extraire_deliberations import construire_url_base, detecter_seance_la_plus_recente


RACINE = Path(__file__).resolve().parent


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
    return parser.parse_args()


def main() -> None:
    args = parser_arguments()

    communes = [commune.strip().lower() for commune in args.communes if commune.strip()]
    if not communes:
        print("Aucune commune fournie.")
        return

    for commune in communes:
        fichier_delib = RACINE / (
            "deliberations_wavre.json" if commune == "wavre" else f"deliberations_{commune}.json"
        )
        url_base = construire_url_base(commune)

        if not args.skip_extraction:
            seance_vue_id, seance_vue_nom = charger_derniere_seance(fichier_delib)
            nouvelle_seance_id, nouvelle_seance_nom = detecter_seance_la_plus_recente(url_base)

            if nouvelle_seance_id and seance_vue_id == nouvelle_seance_id and not args.force:
                print("=" * 80)
                print(f"Aucune nouvelle séance détectée pour {commune}, extraction et analyse ignorées.")
                print("=" * 80)
                continue

            if nouvelle_seance_id is None:
                print(f"⚠ Séance la plus récente inconnue pour {commune}. L'extraction sera tout de même tentée.")

            executer(
                f"Étape 1/2 - Extraction des délibérations ({commune})",
                [sys.executable, "extraire_deliberations.py", "--commune", commune],
            )
        else:
            print(f"Extraction ignorée (--skip-extraction) pour {commune}.\n")

        if args.skip_analyse:
            print(f"Analyse journalistique ignorée (--skip-analyse) pour {commune}.\n")
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
        executer(
            "Compilation HTML multi-communes",
            [sys.executable, "analyser_sujets.py", "--merge-html", "--communes", *communes],
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"ERREUR : {exc}")
        sys.exit(1)
