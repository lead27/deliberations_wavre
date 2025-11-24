import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from extraire_deliberations import detecter_seance_la_plus_recente


RACINE = Path(__file__).resolve().parent
FICHIER_DELIB = RACINE / "deliberations_wavre.json"


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
    return parser.parse_args()


def main() -> None:
    args = parser_arguments()

    if not args.skip_extraction:
        seance_vue_id, seance_vue_nom = charger_derniere_seance(FICHIER_DELIB)
        nouvelle_seance_id, nouvelle_seance_nom = detecter_seance_la_plus_recente()

        if nouvelle_seance_id and seance_vue_id == nouvelle_seance_id and not args.force:
            print("=" * 80)
            print("Aucune nouvelle séance détectée, extraction et analyse ignorées.")
            print("=" * 80)
            return

        if nouvelle_seance_id is None:
            print("⚠ Séance la plus récente inconnue. L'extraction sera tout de même tentée.")

        executer(
            "Étape 1/2 - Extraction des délibérations",
            [sys.executable, "extraire_deliberations.py"],
        )
    else:
        print("Extraction ignorée (--skip-extraction).\n")

    if args.skip_analyse:
        print("Analyse journalistique ignorée (--skip-analyse).\n")
        return

    commande = [sys.executable, "analyser_sujets.py", "--auto"]
    if args.modele:
        commande.extend(["--modele", args.modele])
    if args.skip_html:
        commande.append("--skip-html")
    if args.skip_json:
        commande.append("--skip-json")
    if args.details:
        commande.append("--details")
        commande.extend(str(num) for num in args.details)

    executer("Étape 2/2 - Analyse journalistique et génération des sorties", commande)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover
        print(f"ERREUR : {exc}")
        sys.exit(1)
