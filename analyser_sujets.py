import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI, OpenAIError


MODELE_PAR_DEFAUT = "gpt-4o-mini"


def construire_client_openai() -> OpenAI:
    """Initialise le client OpenAI à partir des variables d'environnement."""
    try:
        return OpenAI()
    except OpenAIError as exc:
        raise RuntimeError(f"Erreur d'initialisation OpenAI : {exc}") from exc


def appeler_modele_json(client: OpenAI, prompt: str, modele: str) -> str:
    """Envoie un prompt et exige une réponse JSON valide."""
    try:
        reponse = client.chat.completions.create(
            model=modele,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Tu es un assistant qui répond toujours avec un JSON valide respectant la demande."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        raise RuntimeError(f"Appel OpenAI échoué : {exc}") from exc

    if not reponse.choices:
        raise RuntimeError("Réponse vide du modèle.")

    texte = (reponse.choices[0].message.content or "").strip()
    if not texte:
        raise RuntimeError("Réponse textuelle vide du modèle.")
    return texte


def appeler_modele_text(client: OpenAI, prompt: str, modele: str) -> str:
    """Envoie un prompt et récupère une réponse textuelle libre."""
    try:
        reponse = client.chat.completions.create(
            model=modele,
            messages=[
                {
                    "role": "system",
                    "content": "Tu es un assistant journalistique qui rédige des synthèses en français.",
                },
                {"role": "user", "content": prompt},
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(f"Appel OpenAI échoué : {exc}") from exc

    if not reponse.choices:
        raise RuntimeError("Réponse vide du modèle.")

    texte = (reponse.choices[0].message.content or "").strip()
    if not texte:
        raise RuntimeError("Réponse textuelle vide du modèle.")
    return texte


def charger_deliberations(fichier: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Charge les délibérations et retourne la liste + métadonnées éventuelles."""
    print(f"Chargement de {fichier}...")
    try:
        with open(fichier, "r", encoding="utf-8") as handle:
            donnees = json.load(handle)
    except FileNotFoundError as err:
        print(f"ERREUR : Le fichier {fichier} est introuvable.")
        print("Lancez d'abord extraire_deliberations.py")
        sys.exit(1)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"JSON invalide dans {fichier} : {err}") from err

    if isinstance(donnees, dict) and "deliberations" in donnees:
        deliberations = donnees["deliberations"]
        meta = donnees.get("seance")
    elif isinstance(donnees, list):
        deliberations = donnees
        meta = None
    else:
        raise RuntimeError("Format de données inattendu pour les délibérations.")

    print(f"✓ {len(deliberations)} délibération(s) chargée(s)\n")
    return deliberations, meta


def creer_resume_court(deliberations: List[Dict[str, Any]]) -> str:
    """Crée un résumé concis de l'ensemble des délibérations."""
    resume = "DÉLIBÉRATIONS DU CONSEIL COMMUNAL:\n\n"
    for index, deliberation in enumerate(deliberations, 1):
        titre = deliberation.get("titre", "Titre inconnu")
        contenu = deliberation.get("contenu", "")
        lignes = contenu.split("\n")

        note_synthese = ""
        capture = False
        for ligne in lignes:
            if "Note de synthèse" in ligne or "note de synthèse" in ligne:
                capture = True
                continue
            if capture:
                if ligne.strip() and not ligne.startswith("Projet de décision"):
                    note_synthese += ligne + " "
                if "Projet de décision" in ligne:
                    break

        if not note_synthese:
            note_synthese = contenu[:200]

        resume += f"{index}. {titre}\n{note_synthese.strip()[:300]}\n\n"
    return resume


def _nettoyer_sortie_json(texte: str) -> str:
    """Supprime les décorations éventuelles autour du JSON (blocs de code, texte parasite)."""
    if "```" in texte:
        morceaux = re.findall(r"```(?:json)?\s*(.*?)```", texte, flags=re.DOTALL)
        if morceaux:
            texte = morceaux[0]
    texte = texte.strip()
    if texte.startswith("{") and texte.endswith("}"):
        return texte
    match = re.search(r"\{.*\}", texte, flags=re.DOTALL)
    if match:
        return match.group(0).strip()
    return texte


def _normaliser_points(points: Any) -> List[str]:
    """Convertit les points à creuser en liste de chaînes."""
    if isinstance(points, list):
        resultat = []
        for item in points:
            if isinstance(item, str):
                propre = item.strip()
                if propre:
                    resultat.append(propre)
        return resultat
    if isinstance(points, str):
        lignes = [segment.strip() for segment in re.split(r"[\n•*-]+", points) if segment.strip()]
        return lignes
    return []


def extraire_topics_depuis_reponse(reponse: str) -> List[Dict[str, Any]]:
    """Parse la réponse JSON d'Ollama pour obtenir la liste des sujets."""
    contenu_json = _nettoyer_sortie_json(reponse)
    try:
        donnees = json.loads(contenu_json)
    except json.JSONDecodeError as err:
        raise RuntimeError("Impossible de décoder la réponse d'Ollama en JSON") from err

    topics_bruts = donnees.get("topics", [])
    sujets: List[Dict[str, Any]] = []
    for brut in topics_bruts:
        if not isinstance(brut, dict):
            continue
        sujet = {
            "titre": brut.get("titre", "").strip(),
            "interet": brut.get("interet", "").strip(),
            "angle": brut.get("angle", "").strip(),
            "points_a_creuser": _normaliser_points(brut.get("points_a_creuser")),
            "references": (brut.get("references", "") or "").strip(),
        }
        if sujet["titre"]:
            sujets.append(sujet)
    return sujets


def analyser_globalement(
    client: OpenAI,
    deliberations: List[Dict[str, Any]],
    modele: str,
    max_sujets: int = 5,
) -> List[Dict[str, Any]]:
    """Interroge l'IA pour obtenir la liste des sujets journalistiques."""
    print("=" * 80)
    print("ANALYSE GLOBALE : Recherche des sujets journalistiques")
    print("=" * 80 + "\n")
    print("L'IA analyse toutes les délibérations (2-3 minutes)...\n")

    resume = creer_resume_court(deliberations)
    prompt = f"""Tu es un journaliste expérimenté qui passe en revue des délibérations d'un conseil communal.

Voici les délibérations récentes du conseil communal de Wavre :

{resume}

Objectif :
- Identifier les sujets journalistiques réellement pertinents (maximum {max_sujets}).
- Ignorer les points purement administratifs ou sans intérêt citoyen.

Critères : impact sur les citoyens, caractère inédit, enjeux financiers importants, potentiel de controverse.

Format de réponse : renvoie UNIQUEMENT un objet JSON valide de la forme
{{
  "topics": [
    {{
      "titre": "...",
      "interet": "...",
      "angle": "...",
      "points_a_creuser": ["...", "..."],
      "references": "..."
    }}
  ]
}}

Règles :
- Ne renvoie aucun texte en dehors de ce JSON.
- Si aucun sujet n'est pertinent, retourne {{ "topics": [] }}.
- Les champs texte doivent être rédigés en français, ton professionnel.
"""

    reponse = appeler_modele_json(client, prompt, modele)
    sujets = extraire_topics_depuis_reponse(reponse)

    print(f"✓ {len(sujets)} sujet(s) proposé(s) par l'IA\n")
    return sujets


def analyser_sujet_specifique(
    client: OpenAI,
    deliberation: Dict[str, Any],
    numero: int,
    modele: str,
) -> str:
    """Analyse détaillée d'une délibération spécifique."""
    print(f"\nAnalyse détaillée du point {numero}...")

    contenu = deliberation.get("contenu", "")
    prompt = f"""Tu es un journaliste qui analyse une délibération de conseil communal.

TITRE : {deliberation.get('titre', 'Titre inconnu')}

CONTENU :
{contenu[:3000]}

Fournis en français une analyse synthétique comprenant :
1. RÉSUMÉ EN 2 PHRASES
2. ENJEUX POUR LES CITOYENS
3. MONTANTS BUDGÉTAIRES (s'il y en a)
4. CONTROVERSES POTENTIELLES
5. QUESTIONS À POSER
6. CONTEXTE COMPLÉMENTAIRE
"""

    return appeler_modele_text(client, prompt, modele)


def sauvegarder_analyse_textuelle(
    sujets: List[Dict[str, Any]],
    analyses_detaillees: Dict[int, str],
    chemin_fichier: str,
    seance: Optional[Dict[str, Any]],
) -> None:
    """Génère le fichier texte lisible rassemblant les sujets."""
    lignes: List[str] = []
    lignes.append("=" * 80)
    lignes.append("ANALYSE JOURNALISTIQUE DES DÉLIBÉRATIONS")
    lignes.append("Conseil communal de Wavre")
    if seance and seance.get("nom"):
        lignes.append(seance["nom"])
    lignes.append("=" * 80 + "\n")

    lignes.append("ANALYSE GLOBALE - TOP SUJETS")
    lignes.append("=" * 80 + "\n")

    if not sujets:
        lignes.append("Aucun sujet journalistique majeur n'a été identifié pour cette séance.\n")
    else:
        lignes.append("Voici les sujets les plus prometteurs :\n")
        for index, sujet in enumerate(sujets, 1):
            lignes.append(f"**{index}. Titre : \"{sujet['titre']}\"**")
            if sujet["interet"]:
                lignes.append(f"**INTÉRÊT :** {sujet['interet']}")
            if sujet["angle"]:
                lignes.append(f"**ANGLE :** {sujet['angle']}")
            if sujet["points_a_creuser"]:
                lignes.append("**POINTS À CREUSER :**")
                for point in sujet["points_a_creuser"]:
                    lignes.append(f"* {point}")
            if sujet["references"]:
                lignes.append(f"**NUMÉROS :** {sujet['references']}")
            lignes.append("")

    lignes.append("=" * 80 + "\n")

    if analyses_detaillees:
        lignes.append("ANALYSES DÉTAILLÉES PAR SUJET")
        lignes.append("=" * 80 + "\n")
        for numero, analyse in analyses_detaillees.items():
            lignes.append(f"\n{'=' * 80}")
            lignes.append(f"DÉLIBÉRATION N°{numero}")
            lignes.append('=' * 80)
            lignes.append("")
            lignes.append(analyse.strip())
            lignes.append("")
    else:
        lignes.append("ANALYSES DÉTAILLÉES PAR SUJET")
        lignes.append("=" * 80 + "\n")
        lignes.append("Aucune analyse détaillée supplémentaire n'a été demandée.\n")

    Path(chemin_fichier).write_text("\n".join(lignes), encoding="utf-8")
    print(f"✓ Analyse globale sauvegardée dans {chemin_fichier}")


def sauvegarder_topics_json(
    sujets: List[Dict[str, Any]],
    chemin_fichier: str,
    seance: Optional[Dict[str, Any]],
) -> None:
    """Sauvegarde les sujets dans un fichier JSON structuré."""
    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "seance": seance,
        "topics": sujets,
    }
    Path(chemin_fichier).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ Résultats structurés sauvegardés dans {chemin_fichier}")


def generer_html(
    sujets: List[Dict[str, Any]],
    chemin_fichier: str,
    seance: Optional[Dict[str, Any]],
) -> None:
    """Produit la page HTML à partir des sujets identifiés."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    sous_titre = "Conseil communal de Wavre"
    if seance and seance.get("nom"):
        sous_titre += f" — {seance['nom']}"

    sections: List[str] = []
    if not sujets:
        sections.append(
            "<p>Aucun sujet journalistique n'a été identifié pour cette séance. "
            "Revenez après la prochaine mise à jour automatique.</p>"
        )
    else:
        sections.append("<section>")
        sections.append("  <h2>Sujets prioritaires pour des articles</h2>")
        for index, sujet in enumerate(sujets, 1):
            sections.append('  <article class="subject">')
            sections.append(f"    <h3>{index}. {html.escape(sujet['titre'])}</h3>")
            if sujet["interet"]:
                sections.append("    <strong>Intérêt</strong>")
                sections.append(f"    <p>{html.escape(sujet['interet'])}</p>")
            if sujet["angle"]:
                sections.append("    <strong>Angle</strong>")
                sections.append(f"    <p>{html.escape(sujet['angle'])}</p>")
            if sujet["points_a_creuser"]:
                sections.append("    <strong>Points à creuser</strong>")
                sections.append("    <ul>")
                for point in sujet["points_a_creuser"]:
                    sections.append(f"      <li>{html.escape(point)}</li>")
                sections.append("    </ul>")
            if sujet["references"]:
                sections.append("    <strong>Références</strong>")
                sections.append(f"    <p>{html.escape(sujet['references'])}</p>")
            sections.append("  </article>")
        sections.append("</section>")

    contenu_section = "\n".join(sections)

    contenu_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Analyse journalistique des délibérations - Conseil communal de Wavre</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      color-scheme: light dark;
    }}

    body {{
      font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
      margin: 0;
      padding: 2rem 1.5rem 3rem;
      background-color: #f3f4f6;
      color: #1f2933;
      display: grid;
      place-items: center;
    }}

    main {{
      width: min(100%, 960px);
      background-color: #ffffff;
      border-radius: 16px;
      box-shadow: 0 18px 35px rgba(15, 23, 42, 0.08);
      padding: 2.5rem;
    }}

    header {{
      text-align: center;
      margin-bottom: 2.5rem;
    }}

    header h1 {{
      margin: 0;
      font-size: 2rem;
      letter-spacing: 0.02em;
    }}

    header p {{
      margin-top: 0.5rem;
      color: #52606d;
      font-size: 1.05rem;
    }}

    section h2 {{
      font-size: 1.65rem;
      margin-bottom: 1rem;
      color: #102a43;
    }}

    .subject {{
      border-top: 1px solid #e4e7eb;
      padding: 1.75rem 0;
    }}

    .subject:first-of-type {{
      border-top: none;
      padding-top: 0;
    }}

    .subject h3 {{
      margin: 0;
      font-size: 1.35rem;
      color: #0b7285;
    }}

    .subject strong {{
      display: inline-block;
      margin-top: 0.75rem;
      font-size: 0.95rem;
      color: #102a43;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}

    .subject p {{
      margin: 0.35rem 0 0.35rem;
      color: #334e68;
      font-size: 1rem;
    }}

    .subject ul {{
      margin: 0.75rem 0 0.5rem 1.25rem;
      color: #243b53;
    }}

    .subject li {{
      margin: 0.25rem 0;
    }}

    footer {{
      margin-top: 2.5rem;
      text-align: center;
      color: #7b8794;
      font-size: 0.9rem;
    }}

    @media (max-width: 600px) {{
      main {{
        padding: 2rem 1.5rem;
      }}

      header h1 {{
        font-size: 1.6rem;
      }}

      .subject h3 {{
        font-size: 1.2rem;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Analyse journalistique des délibérations</h1>
      <p>{html.escape(sous_titre)} &mdash; Mise à jour automatique du {generated_at}</p>
    </header>
    {contenu_section}
    <footer>
      <p>Compilation générée automatiquement à partir des délibérations les plus récentes.</p>
    </footer>
  </main>
</body>
</html>
"""

    Path(chemin_fichier).write_text(contenu_html, encoding="utf-8")
    print(f"✓ Page HTML actualisée dans {chemin_fichier}")


def parser_arguments() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(description="Analyse les délibérations et génère les sujets journalistiques.")
    parser.add_argument("--deliberations", default="deliberations_wavre.json", help="Fichier JSON des délibérations.")
    parser.add_argument("--texte", default="sujets_journalistiques.txt", help="Fichier texte de sortie.")
    parser.add_argument("--json", dest="json_path", default="sujets_journalistiques.json", help="Fichier JSON de sortie.")
    parser.add_argument("--html", default="sujets_journalistiques.html", help="Fichier HTML de sortie.")
    parser.add_argument("--auto", action="store_true", help="Mode automatique (aucune interaction utilisateur).")
    parser.add_argument(
        "--modele",
        default=MODELE_PAR_DEFAUT,
        help="Identifiant du modèle OpenAI à utiliser (ex: gpt-4o-mini).",
    )
    parser.add_argument("--skip-html", action="store_true", help="Ne pas générer la page HTML.")
    parser.add_argument("--skip-json", action="store_true", help="Ne pas générer le fichier JSON.")
    parser.add_argument(
        "--details",
        nargs="*",
        type=int,
        help="Numéros des délibérations à analyser en détail (utile en mode auto).",
    )
    return parser.parse_args()


def main() -> None:
    args = parser_arguments()

    deliberations, seance = charger_deliberations(args.deliberations)

    client = construire_client_openai()

    if not deliberations:
        print("Aucune délibération à analyser. Arrêt.")
        return

    sujets = analyser_globalement(client, deliberations, modele=args.modele)

    analyses_detaillees: Dict[int, str] = {}

    if args.details:
        numeros = [n for n in args.details if 1 <= n <= len(deliberations)]
    elif not args.auto:
        print("=" * 80)
        reponse = input("Voulez-vous des analyses détaillées de certaines délibérations ? (o/n) : ").strip().lower()
        numeros: List[int] = []
        if reponse in {"o", "oui", "y", "yes"}:
            print("\nEntrez les numéros des délibérations à analyser en détail (ex: 1 4 8)")
            print(f"Numéros disponibles : 1 à {len(deliberations)}")
            saisie = input("Numéros : ")
            try:
                numeros = [int(part.strip()) for part in re.split(r"[ ,;]+", saisie) if part.strip()]
                numeros = [n for n in numeros if 1 <= n <= len(deliberations)]
            except ValueError:
                print("Format invalide, analyses détaillées ignorées.")
                numeros = []
    else:
        numeros = []

    for numero in numeros:
        analyse = analyser_sujet_specifique(client, deliberations[numero - 1], numero, modele=args.modele)
        analyses_detaillees[numero] = analyse

    sauvegarder_analyse_textuelle(sujets, analyses_detaillees, args.texte, seance)
    if not args.skip_json:
        sauvegarder_topics_json(sujets, args.json_path, seance)
    if not args.skip_html:
        generer_html(sujets, args.html, seance)

    print("=" * 80)
    print("ANALYSE TERMINÉE !")
    print("=" * 80)
    print(f"\nConsultez :\n  - {args.texte}\n  - {args.json_path}\n  - {args.html if not args.skip_html else '(HTML non généré)'}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - gestion globale des erreurs
        print(f"ERREUR : {exc}")
        sys.exit(1)
