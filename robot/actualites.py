"""Actualités : flux RSS de médias → sélection et réécriture par Gemini → publication."""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
import time
from datetime import datetime
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import feedparser
import requests

from . import visuels
from .base import DOSSIER_SORTIE, maintenant
from .services import UA, Gemini, Publieur

log = logging.getLogger("robot")
ETIQUETTES_VALIDES = {"OFFICIEL", "TRANSFERT", "RUMEUR", "BLESSURE", "SCANDALE", "INFO"}

CONSIGNE = """Tu es le rédacteur en chef d'une page Facebook francophone d'actualité football, suivie par des fans d'Afrique francophone, de France et du Québec.

Compétitions suivies : {competitions}.
Sujets qui intéressent la page : {sujets}.
Ne traite PAS les résultats ou buts des matchs en cours (un autre système s'en charge).

Voici des articles récents (index, source, titre, résumé) :
{articles}

Publications déjà faites récemment par la page (à ne pas répéter, même si une autre source en parle) :
{deja}

Ta mission : choisis AU MAXIMUM {nb} articles qui méritent vraiment d'être publiés (grosses infos, forte réaction attendue des fans). Si rien n'est assez important, renvoie une liste vide. Évite les doublons (même info dans deux sources = un seul choix).

Règles strictes :
- N'invente RIEN. Utilise uniquement les faits présents dans le titre et le résumé.
- Attribue toujours l'info : termine le texte par « 📰 Selon <source> ». Si l'article a été « trouvé en suivant » un journaliste ou un compte ET que le titre montre que l'info vient de lui, écris « 📰 Selon <ce journaliste> (via <média>) ». Sinon, cite seulement le média. N'attribue JAMAIS une info à un journaliste si l'article ne le dit pas.
- Les articles trouvés en suivant un compte peuvent être hors sujet (simple mention du nom) : ignore-les dans ce cas.
- Étiquette : OFFICIEL seulement si la source dit que c'est officiel/confirmé ; RUMEUR pour une info non confirmée (« selon nos informations », « pourrait », « proche de ») ; TRANSFERT, BLESSURE, SCANDALE ou INFO sinon.
- Scandales et polémiques : ton neutre et factuel, présomption d'innocence (« accusé de », « visé par une enquête », « selon <source> »), aucune insulte, rien sur la vie privée sans lien avec le football.
- Texte en français, 2 à 4 lignes courtes, accrocheur, avec 1 à 3 émojis adaptés (🚨 ✍️ 🔥 🤕 ⚠️ 💰…). Pas de hashtags.
- titre_visuel : 4 à 12 mots, percutant, sans émoji, en français.

Réponds UNIQUEMENT avec ce JSON :
{{"publications": [{{"index": 0, "etiquette": "TRANSFERT", "titre_visuel": "...", "texte": "..."}}]}}"""


GOOGLE_NEWS = {
    "fr": "hl=fr&gl=FR&ceid=FR:fr",
    "en": "hl=en-GB&gl=GB&ceid=GB:en",
    "es": "hl=es&gl=ES&ceid=ES:es",
    "it": "hl=it&gl=IT&ceid=IT:it",
    "pt": "hl=pt-PT&gl=PT&ceid=PT:pt-150",
    "nl": "hl=nl&gl=NL&ceid=NL:nl",
    "tr": "hl=tr&gl=TR&ceid=TR:tr",
    "de": "hl=de&gl=DE&ceid=DE:de",
}


def liste_flux(c: dict) -> list[dict]:
    """Tous les flux : RSS directs + recherches Google Actualités (comptes suivis)."""
    flux = [dict(f, genre="rss") for f in c.get("flux_rss", [])]
    for s in c.get("comptes_suivis", []):
        q = quote_plus(f"{s['recherche']} when:1d")
        langue = GOOGLE_NEWS.get(s.get("langue", "fr"), GOOGLE_NEWS["fr"])
        flux.append({
            "nom": s["nom"],
            "url": f"https://news.google.com/rss/search?q={q}&{langue}",
            "genre": "google",
            "jamais_officiel": bool(s.get("jamais_officiel")),
        })
    return flux


def lire_un_flux(flux: dict, limite: float) -> list[dict]:
    try:
        r = requests.get(flux["url"], headers=UA, timeout=20)
        r.raise_for_status()
        f = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001
        log.warning("Flux %s illisible : %s", flux.get("nom"), e)
        return []
    finally:
        if flux.get("genre") == "google":
            time.sleep(1)  # politesse envers Google Actualités
    res = []
    for e in f.entries[:40]:
        t = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(t) if t else maintenant()
        if ts < limite:
            continue
        titre = _nettoyer(e.get("title", ""), 220)
        if not titre:
            continue
        source, suivi = flux.get("nom", "?"), None
        if flux.get("genre") == "google":
            # Titre Google : « Titre de l'article - Nom du média »
            media = _nettoyer((e.get("source") or {}).get("title", ""), 60)
            if not media and " - " in titre:
                media = titre.rsplit(" - ", 1)[1]
            if media and titre.endswith(" - " + media):
                titre = titre[: -len(media) - 3]
            source, suivi = media or "Google Actualités", flux.get("nom")
        lien = e.get("link") or ""
        res.append({
            "id": hashlib.sha1((lien or titre).encode()).hexdigest()[:16],
            "source": source,
            "suivi": suivi,
            "jamais_officiel": flux.get("jamais_officiel", False),
            "titre": titre,
            "resume": "" if flux.get("genre") == "google" else _nettoyer(e.get("summary", ""), 400),
            "lien": lien,
            "date": ts,
        })
    return res


def _cle_titre(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())[:70]


def _nettoyer(s: str, n: int) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    return re.sub(r"\s+", " ", s).strip()[:n]


class Actualites:
    def __init__(self, cfg: dict, etat, gemini: Gemini, pub: Publieur):
        self.cfg = cfg
        self.c = cfg.get("actualites", {})
        self.page = cfg.get("page", {})
        self.etat = etat
        self.gemini = gemini
        self.pub = pub

    # ---------------------------------------------------------------------
    def lancer(self) -> None:
        if self._heure_silence():
            log.info("Actualités : heures de silence, rien n'est publié.")
            return
        restant_jour = int(self.c.get("max_par_jour", 25)) - self.etat.compteur("actus_publiees")
        if restant_jour <= 0:
            log.info("Actualités : maximum du jour atteint.")
            return

        ecart = (maintenant() - self.etat.d.get("dernier_passage_actus", 0)) / 60
        if ecart < float(self.c.get("intervalle_minutes", 15)) - 0.5:
            log.info("Actualités : prochain passage dans %.0f min.", float(self.c.get("intervalle_minutes", 15)) - ecart)
            return
        self.etat.d["dernier_passage_actus"] = maintenant()

        articles = self._lire_flux()
        vus = set(self.etat.d["actus_vues"])
        if not self.etat.d.get("actus_initialisees"):
            # Premier passage : on mémorise l'existant sans rien publier (évite une avalanche).
            self.etat.d["actus_vues"] += [a["id"] for a in articles]
            self.etat.d["actus_initialisees"] = True
            log.info("Actualités : %d articles mémorisés au premier passage (non publiés).", len(articles))
            return

        nouveaux = [a for a in articles if a["id"] not in vus]
        self.etat.d["actus_vues"] += [a["id"] for a in nouveaux]
        if not nouveaux:
            log.info("Actualités : aucun nouvel article.")
            return
        nouveaux = sorted(nouveaux, key=lambda a: a["date"], reverse=True)[:40]
        log.info("Actualités : %d nouvel(s) article(s) à évaluer.", len(nouveaux))

        nb = min(int(self.c.get("max_par_passage", 2)), restant_jour)
        choix = self._choisir(nouveaux, nb)
        for p in choix[:nb]:
            self._publier(p, nouveaux)

    # ---------------------------------------------------------------------
    def _heure_silence(self) -> bool:
        plage = self.c.get("heures_silence") or []
        if len(plage) != 2:
            return False
        try:
            h = datetime.now(ZoneInfo(self.page.get("fuseau_horaire", "UTC"))).hour
        except Exception:  # noqa: BLE001
            h = datetime.utcnow().hour
        debut, fin = int(plage[0]), int(plage[1])
        return debut <= h < fin if debut < fin else (h >= debut or h < fin)

    def _lire_flux(self) -> list[dict]:
        articles, titres_vus = [], set()
        limite = maintenant() - 8 * 3600
        for flux in liste_flux(self.c):
            for a in lire_un_flux(flux, limite):
                cle = _cle_titre(a["titre"])
                if cle in titres_vus:  # même article trouvé par plusieurs recherches
                    continue
                titres_vus.add(cle)
                articles.append(a)
        return articles

    def _choisir(self, articles: list[dict], nb: int) -> list[dict]:
        comps = ", ".join(x["nom"] for x in self.cfg.get("direct", {}).get("competitions", []))
        lignes = []
        for i, a in enumerate(articles):
            suivi = f" ; trouvé en suivant : {a['suivi']}" if a.get("suivi") else ""
            lignes.append(f"[{i}] (média : {a['source']}{suivi}) {a['titre']} — {a['resume']}")
        liste = "\n".join(lignes)
        deja = "\n".join(
            f"- {h['resume']}" for h in self.etat.d["historique"][-30:] if h.get("genre") == "actu"
        ) or "(aucune)"
        consigne = CONSIGNE.format(
            competitions=comps, sujets=" ".join(str(self.c.get("sujets", "")).split()),
            articles=liste, deja=deja, nb=nb,
        )
        try:
            rep = self.gemini.json(consigne)
        except Exception as e:  # noqa: BLE001
            log.error("Actualités : Gemini indisponible : %s", e)
            return []
        pubs = rep.get("publications") if isinstance(rep, dict) else rep
        return [p for p in (pubs or []) if isinstance(p, dict)]

    def _publier(self, p: dict, articles: list[dict]) -> None:
        try:
            a = articles[int(p.get("index"))]
        except (TypeError, ValueError, IndexError):
            return
        etiquette = str(p.get("etiquette", "INFO")).upper()
        if etiquette not in ETIQUETTES_VALIDES:
            etiquette = "INFO"
        texte = str(p.get("texte") or "").strip()
        titre = str(p.get("titre_visuel") or a["titre"]).strip()
        if not texte:
            return
        if a.get("jamais_officiel") and etiquette == "OFFICIEL":
            etiquette = "RUMEUR"
        cite = a["source"].lower() in texte.lower() or (a.get("suivi") or "x" * 50).lower() in texte.lower()
        if not cite:
            texte += f"\n\n📰 Selon {a['source']}"
        if self.c.get("ajouter_lien_source") and a["lien"]:
            texte += f"\n🔗 {a['lien']}"
        tags = self.page.get("hashtags", "")
        if tags:
            texte += f"\n\n{tags}"
        try:
            image = visuels.carte_actu(DOSSIER_SORTIE, self.page, etiquette, titre, a["source"])
            self.pub.publier(image, texte)
        except Exception as e:  # noqa: BLE001
            log.error("Actualités : publication impossible : %s", e)
            return
        self.etat.incrementer("actus_publiees")
        self.etat.ajouter_historique("actu", f"{titre} ({a['source']})")
        self.etat.sauver()
