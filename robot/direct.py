"""Direct des matchs : buts, buts annulés, cartons rouges, scores finaux."""
from __future__ import annotations

import logging
import time

from . import visuels
from .base import DOSSIER_SORTIE, jour_utc, maintenant
from .services import ApiFootball, Publieur

log = logging.getLogger("robot")

EN_COURS = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
TERMINES = {"FT", "AET", "PEN", "AWD", "WO"}
ANNULES = {"PST", "CANC", "ABD"}


def _minute(ev: dict) -> str:
    t = ev.get("time") or {}
    e, x = t.get("elapsed"), t.get("extra")
    if e is None:
        return ""
    return f"{e}+{x}'" if x else f"{e}'"


def _cle(ev: dict) -> str:
    t = ev.get("time") or {}
    return "|".join(
        str(v)
        for v in (
            ev.get("type"),
            ev.get("detail"),
            t.get("elapsed"),
            t.get("extra"),
            (ev.get("player") or {}).get("id") or (ev.get("player") or {}).get("name"),
            (ev.get("team") or {}).get("id"),
        )
    )


def _categorie(ev: dict) -> str | None:
    typ = (ev.get("type") or "").lower()
    det = (ev.get("detail") or "").lower()
    if typ == "goal":
        if "missed" in det:
            return "penalty_rate"
        if "own" in det:
            return "csc"
        if "penalty" in det:
            return "but_penalty"
        return "but"
    if typ == "card" and ("red" in det or "second yellow" in det):
        return "rouge"
    if typ == "var" and "goal" in det and ("cancel" in det or "disallow" in det):
        return "but_annule"
    return None


def _equipes(m: dict) -> tuple[dict, dict]:
    t = m["teams"]
    return (
        {"nom": t["home"]["name"], "logo": t["home"].get("logo"), "id": t["home"]["id"]},
        {"nom": t["away"]["name"], "logo": t["away"].get("logo"), "id": t["away"]["id"]},
    )


def _score(m: dict) -> str:
    g = m.get("goals") or {}
    return f"{g.get('home') if g.get('home') is not None else 0} - {g.get('away') if g.get('away') is not None else 0}"


class Direct:
    def __init__(self, cfg: dict, etat, api: ApiFootball, pub: Publieur):
        self.cfg = cfg
        self.c = cfg.get("direct", {})
        self.page = cfg.get("page", {})
        self.etat = etat
        self.api = api
        self.pub = pub
        self.noms = {int(x["id"]): x["nom"] for x in self.c.get("competitions", [])}
        self.pro = str(self.c.get("forfait_api_football", "free")).lower() != "free"

    # ---------------------------------------------------------------------
    def lancer(self) -> None:
        self._programme_du_jour()
        actifs = self._matchs_actifs()
        if not actifs:
            log.info("Direct : aucun match en cours dans vos compétitions.")
            return
        d = self.etat.d
        if not self.pro:
            ecart = (maintenant() - d.get("dernier_appel_direct", 0)) / 60
            if ecart < float(self.c.get("intervalle_minutes_gratuit", 10)) - 0.5:
                log.info("Direct (forfait gratuit) : prochain contrôle dans %.0f min.",
                         float(self.c.get("intervalle_minutes_gratuit", 10)) - ecart)
                return
            if self.etat.compteur("appels_api_football") >= 95:
                log.warning("Direct : quota gratuit du jour presque atteint, pause jusqu'à demain.")
                return
        # Forfait Pro : contrôle chaque minute pendant ~4 min (le passage suivant prend le relais)
        fin = maintenant() + (4 * 60 if self.pro else 0)
        while True:
            d["dernier_appel_direct"] = maintenant()
            matchs = self.api.matchs_par_ids(actifs)
            for m in matchs:
                self._traiter(m)
            self.etat.sauver()
            actifs = self._matchs_actifs()
            if not actifs or maintenant() + 60 > fin:
                break
            time.sleep(60)

    # ---------------------------------------------------------------------
    def _programme_du_jour(self) -> None:
        d = self.etat.d
        jour = jour_utc()
        if d.get("programme_jour") == jour and maintenant() - d.get("programme_maj", 0) < 6 * 3600:
            return
        matchs = self.api.matchs_du_jour(jour, set(self.noms))
        for m in matchs:
            fid = str(m["fixture"]["id"])
            info = d["matchs"].setdefault(fid, {})
            dom, ext = _equipes(m)
            info.update(
                coup_envoi=m["fixture"]["timestamp"],
                competition=self.noms.get(m["league"]["id"], m["league"]["name"]),
                affiche=f"{dom['nom']} - {ext['nom']}",
            )
            if m["fixture"]["status"]["short"] in ANNULES:
                info["fini"] = True
        d["programme_jour"] = jour
        d["programme_maj"] = maintenant()
        log.info("Programme du jour : %d match(s) dans vos compétitions.", len(matchs))

    def _matchs_actifs(self) -> list[int]:
        now = maintenant()
        return [
            int(fid)
            for fid, m in self.etat.d["matchs"].items()
            if not m.get("fini") and m.get("coup_envoi", 0) - 180 <= now <= m.get("coup_envoi", 0) + 190 * 60
        ]

    # ---------------------------------------------------------------------
    def _traiter(self, m: dict) -> None:
        fid = str(m["fixture"]["id"])
        info = self.etat.d["matchs"].setdefault(fid, {"coup_envoi": m["fixture"]["timestamp"]})
        vus = self.etat.d["evenements_vus"].setdefault(fid, [])
        statut = m["fixture"]["status"]["short"]
        ecoule = m["fixture"]["status"].get("elapsed") or 0
        comp = self.noms.get(m["league"]["id"], m["league"]["name"])
        dom, ext = _equipes(m)
        publier = self.c.get("publier", {})
        delai = int(self.c.get("delai_max_minutes", 20))

        if statut in ANNULES:
            info["fini"] = True
            return

        if statut in EN_COURS:
            info["suivi"] = True
        if statut in EN_COURS and not info.get("envoi_publie"):
            info["envoi_publie"] = True
            if publier.get("coups_envoi") and ecoule <= 5:
                self._poster(
                    "COUP D'ENVOI", comp, dom, ext, m,
                    f"🟢 C'est parti ! {dom['nom']} - {ext['nom']}\n🏆 {comp}", "",
                )

        for ev in m.get("events") or []:
            cle = _cle(ev)
            if cle in vus:
                continue
            vus.append(cle)
            cat = _categorie(ev)
            if not cat:
                continue
            ev_min = (ev.get("time") or {}).get("elapsed") or 0
            if statut not in EN_COURS:
                continue  # match terminé : les buteurs figurent dans le score final
            if ecoule - ev_min > delai:
                continue  # trop ancien : on ne publie pas en retard
            self._evenement(cat, ev, m, comp, dom, ext, publier)

        if statut in TERMINES and not info.get("fini"):
            info["fini"] = True
            recent = maintenant() - info.get("coup_envoi", 0) < 150 * 60
            if publier.get("scores_finaux", True) and (info.get("suivi") or recent):
                self._score_final(m, comp, dom, ext)

    # ---------------------------------------------------------------------
    def _evenement(self, cat, ev, m, comp, dom, ext, publier) -> None:
        joueur = (ev.get("player") or {}).get("name") or "?"
        passeur = (ev.get("assist") or {}).get("name")
        equipe = (ev.get("team") or {}).get("name") or ""
        mn = _minute(ev)
        sc = _score(m).replace(" ", "")
        ligne_score = f"{dom['nom']} {sc} {ext['nom']}"
        tags = self.page.get("hashtags", "")

        if cat in ("but", "but_penalty", "csc") and publier.get("buts", True):
            if cat == "csc":
                titre, txt = "CSC !", f"😱 CSC de {joueur} ({mn})"
            elif cat == "but_penalty":
                titre, txt = "BUUUT !", f"⚽ BUUUT sur penalty ! {joueur} {mn}"
            else:
                titre, txt = "BUUUT !", f"⚽ BUUUT ! {joueur} {mn}"
            l2 = f"Passe décisive : {passeur}" if passeur and cat == "but" else equipe
            texte = f"{txt}\n\n{ligne_score}\n🏆 {comp}"
            if passeur and cat == "but":
                texte += f"\n🅰️ Passe décisive : {passeur}"
            self._poster(titre, comp, dom, ext, m, f"{texte}\n\n{tags}".strip(), f"{joueur} {mn}", l2)

        elif cat == "penalty_rate" and publier.get("penaltys_rates", True):
            self._poster(
                "PENALTY RATÉ", comp, dom, ext, m,
                f"❌ Penalty raté par {joueur} ({equipe}) {mn}\n\n{ligne_score}\n🏆 {comp}\n\n{tags}".strip(),
                f"{joueur} {mn}", equipe, couleur=(255, 171, 0),
            )

        elif cat == "but_annule" and publier.get("buts_annules_var", True):
            self._poster(
                "BUT ANNULÉ", comp, dom, ext, m,
                f"🚫 BUT ANNULÉ par la VAR ! {mn}\n{equipe}\n\n{ligne_score}\n🏆 {comp}\n\n{tags}".strip(),
                f"VAR · {mn}", equipe, couleur=(255, 82, 82),
            )

        elif cat == "rouge" and publier.get("cartons_rouges", True):
            self._poster(
                "CARTON ROUGE", comp, dom, ext, m,
                f"🟥 CARTON ROUGE ! {joueur} ({equipe}) {mn}\n\n{ligne_score}\n🏆 {comp}\n\n{tags}".strip(),
                f"{joueur} {mn}", equipe, couleur=(255, 23, 68),
            )

    def _score_final(self, m, comp, dom, ext) -> None:
        sc = _score(m)
        buteurs = []
        for ev in m.get("events") or []:
            cat = _categorie(ev)
            if cat in ("but", "but_penalty", "csc"):
                nom = (ev.get("player") or {}).get("name") or "?"
                suffixe = " (csc)" if cat == "csc" else " (pen.)" if cat == "but_penalty" else ""
                buteurs.append(f"{nom} {_minute(ev)}{suffixe}")
        pen = (m.get("score") or {}).get("penalty") or {}
        ligne_tab = ""
        if pen.get("home") is not None:
            ligne_tab = f"Tirs au but : {pen['home']} - {pen['away']}"
        texte = f"🔚 SCORE FINAL\n\n{dom['nom']} {sc.replace(' ', '')} {ext['nom']}\n🏆 {comp}"
        if ligne_tab:
            texte += f"\n🎯 {ligne_tab}"
        if buteurs:
            texte += "\n\n⚽ " + "\n⚽ ".join(buteurs)
        texte += f"\n\n{self.page.get('hashtags', '')}"
        self._poster("SCORE FINAL", comp, dom, ext, m, texte.strip(), ligne_tab, ", ".join(buteurs[:4]),
                     couleur=(255, 255, 255))

    def _poster(self, titre, comp, dom, ext, m, texte, ligne1, ligne2="", couleur=None) -> None:
        try:
            image = visuels.carte_match(
                DOSSIER_SORTIE, self.page, titre, comp, dom, ext, _score(m), ligne1, ligne2,
                logos=self.c.get("logos_des_clubs", True), couleur_titre=couleur,
            )
            self.pub.publier(image, texte)
        except Exception as e:  # noqa: BLE001 — une erreur ne doit pas bloquer les autres matchs
            log.error("Publication impossible (%s) : %s", titre, e)
            return
        self.etat.ajouter_historique("direct", texte.splitlines()[0] + " " + f"{dom['nom']}-{ext['nom']}")
        self.etat.sauver()
