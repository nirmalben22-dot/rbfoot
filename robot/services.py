"""Connexions aux services extérieurs : API-Football, Gemini, Facebook."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger("robot")
UA = {"User-Agent": "Mozilla/5.0 (robot-foot; +https://github.com)"}


class ErreurService(Exception):
    pass


# =========================================================================
#  API-FOOTBALL
# =========================================================================
class ApiFootball:
    BASE = "https://v3.football.api-sports.io"

    def __init__(self, cle: str, etat=None):
        self.cle = cle
        self.etat = etat
        self.restant: int | None = None

    def get(self, chemin: str, **params) -> list:
        r = requests.get(
            f"{self.BASE}/{chemin}",
            headers={"x-apisports-key": self.cle},
            params=params,
            timeout=25,
        )
        if self.etat is not None and chemin != "status":
            self.etat.incrementer("appels_api_football")
        rest = r.headers.get("x-ratelimit-requests-remaining")
        if rest is not None and rest.lstrip("-").isdigit():
            self.restant = int(rest)
        if r.status_code != 200:
            raise ErreurService(f"API-Football HTTP {r.status_code} : {r.text[:200]}")
        data = r.json()
        erreurs = data.get("errors")
        if erreurs:
            if isinstance(erreurs, dict):
                msg = " ; ".join(f"{k}: {v}" for k, v in erreurs.items())
            else:
                msg = str(erreurs)
            if isinstance(erreurs, dict) and "plan" in erreurs:
                msg += (
                    "  →  Votre forfait gratuit API-Football ne donne pas accès à cette saison. "
                    "Passez au forfait Pro pour le direct."
                )
            raise ErreurService(f"API-Football : {msg}")
        return data.get("response") or []

    def statut(self) -> dict:
        r = requests.get(f"{self.BASE}/status", headers={"x-apisports-key": self.cle}, timeout=20)
        data = r.json()
        if data.get("errors"):
            raise ErreurService(f"API-Football : {data['errors']}")
        return data.get("response") or {}

    def matchs_du_jour(self, jour: str, ids_competitions: set[int]) -> list:
        tous = self.get("fixtures", date=jour, timezone="UTC")
        return [m for m in tous if m.get("league", {}).get("id") in ids_competitions]

    def matchs_par_ids(self, ids: list[int]) -> list:
        res = []
        for i in range(0, len(ids), 20):  # 20 matchs maximum par appel
            lot = ids[i : i + 20]
            res += self.get("fixtures", ids="-".join(str(x) for x in lot))
        return res


# =========================================================================
#  GEMINI
# =========================================================================
class Gemini:
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, cle: str, modeles: list[str]):
        self.cle = cle
        self.modeles = modeles

    def json(self, consigne: str, temperature: float = 0.4) -> dict:
        derniere = None
        for modele in self.modeles:
            try:
                r = requests.post(
                    f"{self.BASE}/{modele}:generateContent",
                    headers={"x-goog-api-key": self.cle, "Content-Type": "application/json"},
                    json={
                        "contents": [{"role": "user", "parts": [{"text": consigne}]}],
                        "generationConfig": {
                            "temperature": temperature,
                            "responseMimeType": "application/json",
                        },
                    },
                    timeout=90,
                )
            except requests.RequestException as e:
                derniere = f"{modele} : {e}"
                continue
            if r.status_code in (404, 429, 500, 503) or (
                r.status_code == 400 and "model" in r.text.lower()
            ):
                derniere = f"{modele} : HTTP {r.status_code}"
                log.info("Gemini %s indisponible (HTTP %s), modèle suivant…", modele, r.status_code)
                continue
            if r.status_code != 200:
                raise ErreurService(f"Gemini HTTP {r.status_code} : {r.text[:300]}")
            data = r.json()
            try:
                parties = data["candidates"][0]["content"]["parts"]
                texte = "".join(p.get("text", "") for p in parties).strip()
            except (KeyError, IndexError):
                derniere = f"{modele} : réponse vide ({str(data)[:150]})"
                continue
            texte = texte.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                return json.loads(texte)
            except json.JSONDecodeError:
                derniere = f"{modele} : JSON invalide"
                continue
        raise ErreurService(f"Aucun modèle Gemini n'a répondu ({derniere})")


# =========================================================================
#  FACEBOOK (Graph API) — avec un mode « test » qui ne publie rien
# =========================================================================
class Publieur:
    def __init__(self, page_id: str, jeton: str, version: str, test: bool, dossier_sortie: Path):
        self.page_id = page_id
        self.jeton = jeton
        self.version = version
        self.test = test
        self.sortie = dossier_sortie
        self.sortie.mkdir(parents=True, exist_ok=True)
        self.nb = 0

    def verifier(self) -> str:
        r = requests.get(
            f"https://graph.facebook.com/{self.version}/{self.page_id}",
            params={"fields": "name,id", "access_token": self.jeton},
            timeout=20,
        )
        data = r.json()
        if "error" in data:
            raise ErreurService(f"Facebook : {data['error'].get('message')}")
        return data.get("name", "?")

    def publier(self, image: Path, texte: str) -> str:
        self.nb += 1
        if self.test:
            (self.sortie / f"{image.stem}.txt").write_text(texte, encoding="utf-8")
            log.info("[TEST, non publié] %s\n%s", image.name, texte)
            return "test"
        for essai in range(3):
            with open(image, "rb") as f:
                r = requests.post(
                    f"https://graph.facebook.com/{self.version}/{self.page_id}/photos",
                    data={"message": texte, "published": "true", "access_token": self.jeton},
                    files={"source": (image.name, f, "image/png")},
                    timeout=60,
                )
            data = r.json() if r.content else {}
            if r.status_code == 200 and "id" in data:
                log.info("Publié sur Facebook (%s) : %s", data["id"], texte.splitlines()[0])
                time.sleep(2)
                return data["id"]
            err = (data.get("error") or {}).get("message", r.text[:200])
            if r.status_code >= 500 and essai < 2:
                time.sleep(5)
                continue
            raise ErreurService(f"Facebook a refusé la publication : {err}")
        raise ErreurService("Facebook indisponible")
