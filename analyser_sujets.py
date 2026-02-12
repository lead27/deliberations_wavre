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


def _nom_commune_affichage(commune: str) -> str:
    return commune.replace("-", " ").title()


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
    """Parse la réponse JSON pour obtenir la liste des points."""
    contenu_json = _nettoyer_sortie_json(reponse)
    try:
        donnees = json.loads(contenu_json)
    except json.JSONDecodeError as err:
        raise RuntimeError("Impossible de décoder la réponse du modèle en JSON") from err

    topics_bruts = donnees.get("points", [])
    sujets: List[Dict[str, Any]] = []
    for brut in topics_bruts:
        if not isinstance(brut, dict):
            continue
        sujet = {
            "titre": (brut.get("titre", "") or "").strip(),
            "description": (brut.get("description", "") or "").strip(),
        }
        if sujet["titre"]:
            sujets.append(sujet)
    return sujets


def analyser_globalement(
    client: OpenAI,
    deliberations: List[Dict[str, Any]],
    modele: str,
    commune_nom: str,
    max_sujets: int = 5,
) -> List[Dict[str, Any]]:
    """Interroge l'IA pour obtenir la liste des points abordés."""
    print("=" * 80)
    print("ANALYSE GLOBALE : Liste des points abordés")
    print("=" * 80 + "\n")
    print("L'IA analyse toutes les délibérations (2-3 minutes)...\n")

    resume = creer_resume_court(deliberations)
    prompt = f"""Tu es un journaliste expérimenté qui passe en revue des délibérations d'un conseil communal.

Voici les délibérations récentes du conseil communal de {commune_nom} :

{resume}

Objectif :
- Lister tous les points abordés, dans l'ordre du document.
- Ne rien filtrer. Une entrée par point, même si le point semble technique ou administratif.
- Fournir 1 à 3 phrases de description, sans proposer d'angle, d'enjeux ou de questions.

Format de réponse : renvoie UNIQUEMENT un objet JSON valide de la forme
{{
  "points": [
    {{
      "titre": "...",
      "description": "..."
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

    print(f"✓ {len(sujets)} point(s) listé(s) par l'IA\n")
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
    commune_nom: str,
) -> None:
    """Génère le fichier texte lisible rassemblant les sujets."""
    lignes: List[str] = []
    lignes.append("=" * 80)
    lignes.append("ANALYSE JOURNALISTIQUE DES DÉLIBÉRATIONS")
    lignes.append(f"Conseil communal de {commune_nom}")
    if seance and seance.get("nom"):
        lignes.append(seance["nom"])
    lignes.append("=" * 80 + "\n")

    lignes.append("ANALYSE GLOBALE - LISTE DES POINTS")
    lignes.append("=" * 80 + "\n")

    if not sujets:
        lignes.append("Aucun point n'a été identifié pour cette séance.\n")
    else:
        lignes.append("Voici la liste des points dans l'ordre :\n")
        for index, sujet in enumerate(sujets, 1):
            lignes.append(f"**{index}. Titre : \"{sujet['titre']}\"**")
            if sujet.get("description"):
                lignes.append(f"**DESCRIPTION :** {sujet['description']}")
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
    commune_nom: str,
) -> None:
    """Sauvegarde les sujets dans un fichier JSON structuré."""
    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "commune": commune_nom,
        "seance": seance,
        "points": sujets,
    }
    Path(chemin_fichier).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ Résultats structurés sauvegardés dans {chemin_fichier}")


def generer_html(
    sujets: List[Dict[str, Any]],
    chemin_fichier: str,
    seance: Optional[Dict[str, Any]],
    commune_nom: str,
) -> None:
    """Produit la page HTML à partir des sujets identifiés."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    seance_nom = seance.get("nom") if seance else None
    table_rows = _table_row_html(commune_nom, seance_nom, sujets)
    contenu_section = f"""<section class="table-section">
      <table>
        <thead>
          <tr>
            <th>Commune</th>
            <th>Date du dernier conseil</th>
            <th>Type</th>
            <th>Points à l'ordre du jour</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </section>"""

    contenu_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Analyse journalistique des délibérations - Conseil communal de {html.escape(commune_nom)}</title>
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

    .table-section {{
      margin-top: 2rem;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.98rem;
    }}

    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 0.85rem 0.75rem;
      border-bottom: 1px solid #e4e7eb;
    }}

    th {{
      background: #f0f4f8;
      color: #102a43;
      font-weight: 600;
      font-size: 0.95rem;
    }}

    td:first-child {{
      font-weight: 600;
      color: #0b7285;
      width: 18%;
    }}

    td:nth-child(2) {{
      width: 20%;
      color: #334e68;
    }}

    td:nth-child(3) {{
      width: 16%;
      color: #52606d;
    }}

    td:nth-child(4) {{
      width: 46%;
    }}

    details {{
      margin-bottom: 0.5rem;
      background: #ffffff;
      border-radius: 8px;
      padding: 0.35rem 0.6rem;
      border: 1px solid #e4e7eb;
    }}

    details[open] {{
      border-color: #9fb3c8;
      background: #f8fafc;
    }}

    summary {{
      cursor: pointer;
      font-weight: 600;
      color: #0b7285;
      list-style: none;
    }}

    summary::-webkit-details-marker {{
      display: none;
    }}

    .point-description {{
      margin-top: 0.5rem;
      color: #334e68;
      font-size: 0.95rem;
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

      table {{
        font-size: 0.95rem;
      }}

      td:first-child {{
        width: auto;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Analyse journalistique des délibérations</h1>
      <p>Conseil communal de {html.escape(commune_nom)} &mdash; Mise à jour automatique du {generated_at}</p>
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


def charger_topics_json(chemin_fichier: str) -> Dict[str, Any]:
    """Charge un fichier JSON de sujets généré par analyser_sujets.py."""
    try:
        with open(chemin_fichier, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as err:
        raise RuntimeError(f"Fichier introuvable : {chemin_fichier}") from err
    except json.JSONDecodeError as err:
        raise RuntimeError(f"JSON invalide dans {chemin_fichier} : {err}") from err


def _sections_html_pour_sujets(sujets: List[Dict[str, Any]]) -> str:
    if not sujets:
        return (
            "<p>Aucun point n'a été identifié pour cette séance. "
            "Revenez après la prochaine mise à jour automatique.</p>"
        )

    sections: List[str] = []
    sections.append("<section>")
    sections.append("  <h3>Liste des points abordés</h3>")
    for index, sujet in enumerate(sujets, 1):
        sections.append('  <article class="subject">')
        sections.append(f"    <h4>{index}. {html.escape(sujet.get('titre', ''))}</h4>")
        description = sujet.get("description")
        if description:
            sections.append("    <strong>Description</strong>")
            sections.append(f"    <p>{html.escape(description)}</p>")
        sections.append("  </article>")
    sections.append("</section>")
    return "\n".join(sections)


def _extraire_date_et_type_seance(seance_nom: Optional[str]) -> Tuple[str, str]:
    if not seance_nom:
        return "", ""
    if "—" in seance_nom:
        parts = [part.strip() for part in seance_nom.split("—", 1)]
    elif " - " in seance_nom:
        parts = [part.strip() for part in seance_nom.split(" - ", 1)]
    else:
        return seance_nom.strip(), ""
    if len(parts) == 2:
        return parts[0], parts[1]
    return seance_nom.strip(), ""


def _points_html(sujets: List[Dict[str, Any]]) -> str:
    if not sujets:
        return "<p>Aucun point n'a été identifié.</p>"
    elements: List[str] = []
    for index, sujet in enumerate(sujets, 1):
        titre = html.escape(sujet.get("titre", ""))
        description = html.escape(sujet.get("description", "")) if sujet.get("description") else ""
        elements.append("<details>")
        elements.append(f"  <summary>{index}. {titre}</summary>")
        if description:
            elements.append(f"  <p class=\"point-description\">{description}</p>")
        elements.append("</details>")
    return "\n".join(elements)


def _table_row_html(commune_nom: str, seance_nom: Optional[str], sujets: List[Dict[str, Any]]) -> str:
    date_seance, type_seance = _extraire_date_et_type_seance(seance_nom)
    points_html = _points_html(sujets)
    return (
        "<tr>"
        f"<td>{html.escape(commune_nom)}</td>"
        f"<td>{html.escape(date_seance)}</td>"
        f"<td>{html.escape(type_seance)}</td>"
        f"<td>{points_html}</td>"
        "</tr>"
    )


def _build_table_html(rows: List[str]) -> str:
    return f"""<section class="table-section">
      <table>
        <thead>
          <tr>
            <th>Commune</th>
            <th>Date du dernier conseil</th>
            <th>Type</th>
            <th>Points à l'ordre du jour</th>
          </tr>
        </thead>
        <tbody>
          {' '.join(rows)}
        </tbody>
      </table>
    </section>"""


def _group_tables_html(
    rows: List[str],
    group_labels: Optional[List[str]],
    group_sizes: Optional[List[int]],
) -> str:
    if not group_labels or not group_sizes or len(group_labels) != len(group_sizes):
        return _build_table_html(rows)

    if sum(group_sizes) != len(rows):
        return _build_table_html(rows)

    sections: List[str] = []
    index = 0
    for label, size in zip(group_labels, group_sizes):
        group_rows = rows[index : index + size]
        index += size
        sections.append(f"<section class=\"group\"><h2>{html.escape(label)}</h2></section>")
        sections.append(_build_table_html(group_rows))
    return "\n".join(sections)


def generer_html_multi(
    sujets_par_commune: List[Dict[str, Any]],
    chemin_fichier: str,
    group_labels: Optional[List[str]] = None,
    group_sizes: Optional[List[int]] = None,
) -> None:
    """Produit une page HTML unique regroupant plusieurs communes."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows: List[str] = []
    for bloc in sujets_par_commune:
        nom = bloc.get("commune_nom") or "Commune"
        seance_nom = bloc.get("seance_nom")
        rows.append(_table_row_html(nom, seance_nom, bloc.get("topics", [])))

    contenu_section = _group_tables_html(rows, group_labels, group_sizes)

    contenu_html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Analyse journalistique des délibérations - Communes</title>
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
      margin-bottom: 2rem;
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

    .table-section {{
      margin-top: 2rem;
    }}

    .group h2 {{
      margin: 2rem 0 0.75rem;
      font-size: 1.6rem;
      color: #102a43;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.98rem;
    }}

    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 0.85rem 0.75rem;
      border-bottom: 1px solid #e4e7eb;
    }}

    th {{
      background: #f0f4f8;
      color: #102a43;
      font-weight: 600;
      font-size: 0.95rem;
    }}

    td:first-child {{
      font-weight: 600;
      color: #0b7285;
      width: 18%;
    }}

    td:nth-child(2) {{
      width: 20%;
      color: #334e68;
    }}

    td:nth-child(3) {{
      width: 16%;
      color: #52606d;
    }}

    td:nth-child(4) {{
      width: 46%;
    }}

    details {{
      margin-bottom: 0.5rem;
      background: #ffffff;
      border-radius: 8px;
      padding: 0.35rem 0.6rem;
      border: 1px solid #e4e7eb;
    }}

    details[open] {{
      border-color: #9fb3c8;
      background: #f8fafc;
    }}

    summary {{
      cursor: pointer;
      font-weight: 600;
      color: #0b7285;
      list-style: none;
    }}

    summary::-webkit-details-marker {{
      display: none;
    }}

    .point-description {{
      margin-top: 0.5rem;
      color: #334e68;
      font-size: 0.95rem;
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

      table {{
        font-size: 0.95rem;
      }}

      td:first-child {{
        width: auto;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Analyse journalistique des délibérations</h1>
      <p>Mise à jour automatique du {generated_at}</p>
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
    print(f"✓ Page HTML multi-communes actualisée dans {chemin_fichier}")


def parser_arguments() -> argparse.Namespace:
    """Analyse les arguments de la ligne de commande."""
    parser = argparse.ArgumentParser(description="Analyse les délibérations et génère les sujets journalistiques.")
    parser.add_argument("--commune", default="wavre", help="Slug de la commune (ex: wavre, incourt).")
    parser.add_argument(
        "--communes",
        nargs="*",
        help="Liste des communes à compiler (utilisé avec --merge-html).",
    )
    parser.add_argument("--deliberations", default=None, help="Fichier JSON des délibérations.")
    parser.add_argument("--texte", default=None, help="Fichier texte de sortie.")
    parser.add_argument("--json", dest="json_path", default=None, help="Fichier JSON de sortie.")
    parser.add_argument("--html", default=None, help="Fichier HTML de sortie.")
    parser.add_argument("--auto", action="store_true", help="Mode automatique (aucune interaction utilisateur).")
    parser.add_argument(
        "--modele",
        default=MODELE_PAR_DEFAUT,
        help="Identifiant du modèle OpenAI à utiliser (ex: gpt-4o-mini).",
    )
    parser.add_argument("--skip-html", action="store_true", help="Ne pas générer la page HTML.")
    parser.add_argument("--skip-json", action="store_true", help="Ne pas générer le fichier JSON.")
    parser.add_argument(
        "--merge-html",
        action="store_true",
        help="Génère une seule page HTML regroupant plusieurs communes.",
    )
    parser.add_argument(
        "--group-labels",
        nargs="*",
        help="Libellés de groupes pour la page HTML multi-communes (ex: 'Brabant wallon' 'Namur').",
    )
    parser.add_argument(
        "--group-sizes",
        nargs="*",
        type=int,
        help="Nombre de communes par groupe, dans l'ordre des libellés.",
    )
    parser.add_argument(
        "--details",
        nargs="*",
        type=int,
        help="Numéros des délibérations à analyser en détail (utile en mode auto).",
    )
    return parser.parse_args()


def main() -> None:
    args = parser_arguments()

    if args.merge_html:
        communes = [commune.strip().lower() for commune in (args.communes or []) if commune.strip()]
        if not communes:
            print("Aucune commune fournie pour la compilation HTML.")
            return
        html_path = args.html or "sujets_journalistiques.html"
        blocs: List[Dict[str, Any]] = []
        for commune in communes:
            json_path = (
                "sujets_journalistiques.json"
                if commune == "wavre"
                else f"sujets_journalistiques_{commune}.json"
            )
            try:
                donnees = charger_topics_json(json_path)
            except RuntimeError as exc:
                print(f"⚠ {exc}. Commune ignorée pour la compilation HTML.")
                continue
            seance = donnees.get("seance") or {}
            commune_nom = donnees.get("commune") or _nom_commune_affichage(commune)
            blocs.append(
                {
                    "commune_nom": commune_nom,
                    "anchor": commune,
                    "seance_nom": seance.get("nom"),
                    "generated_at": donnees.get("generated_at"),
                    "topics": donnees.get("points", []),
                }
            )
        generer_html_multi(
            blocs,
            html_path,
            group_labels=args.group_labels,
            group_sizes=args.group_sizes,
        )
        return

    commune_slug = args.commune.strip().lower()
    commune_nom = _nom_commune_affichage(commune_slug)

    deliberations_path = args.deliberations
    if deliberations_path is None:
        deliberations_path = "deliberations_wavre.json" if commune_slug == "wavre" else f"deliberations_{commune_slug}.json"
    texte_path = args.texte
    if texte_path is None:
        texte_path = "sujets_journalistiques.txt" if commune_slug == "wavre" else f"sujets_journalistiques_{commune_slug}.txt"
    json_path = args.json_path
    if json_path is None:
        json_path = "sujets_journalistiques.json" if commune_slug == "wavre" else f"sujets_journalistiques_{commune_slug}.json"
    html_path = args.html
    if html_path is None:
        html_path = "sujets_journalistiques.html" if commune_slug == "wavre" else f"sujets_journalistiques_{commune_slug}.html"

    deliberations, seance = charger_deliberations(deliberations_path)

    client = construire_client_openai()

    if not deliberations:
        print("Aucune délibération à analyser. Arrêt.")
        return

    sujets = analyser_globalement(client, deliberations, modele=args.modele, commune_nom=commune_nom)

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

    sauvegarder_analyse_textuelle(sujets, analyses_detaillees, texte_path, seance, commune_nom)
    if not args.skip_json:
        sauvegarder_topics_json(sujets, json_path, seance, commune_nom)
    if not args.skip_html:
        generer_html(sujets, html_path, seance, commune_nom)

    print("=" * 80)
    print("ANALYSE TERMINÉE !")
    print("=" * 80)
    print(f"\nConsultez :\n  - {texte_path}\n  - {json_path}\n  - {html_path if not args.skip_html else '(HTML non généré)'}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - gestion globale des erreurs
        print(f"ERREUR : {exc}")
        sys.exit(1)
