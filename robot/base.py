"""Outils communs : configuration, mémoire (state.json), journal."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
FICHIER_CONFIG = RACINE / "config.yaml"
FICHIER_ETAT = RACINE / "state.json"
DOSSIER_SORTIE = RACINE / "sortie"
DOSSIER_POLICES = RACINE / "fonts"

log = logging.getLogger("robot")


def configurer_journal() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def charger_config(chemin: Path = FICHIER_CONFIG) -> dict:
    with open(chemin, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def secret(nom: str) -> str:
    return (os.environ.get(nom) or "").strip()


def maintenant() -> float:
    return time.time()


def jour_utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


class Etat:
    """Mémoire persistante du robot, enregistrée dans state.json et sauvegardée par GitHub."""

    def __init__(self, chemin: Path = FICHIER_ETAT):
        self.chemin = chemin
        try:
            self.d = json.loads(chemin.read_text(encoding="utf-8")) if chemin.exists() else {}
        except (OSError, json.JSONDecodeError):
            log.warning("Mémoire illisible, on repart de zéro.")
            self.d = {}
        self.d.setdefault("matchs", {})
        self.d.setdefault("evenements_vus", {})
        self.d.setdefault("actus_vues", [])
        self.d.setdefault("historique", [])
        self.d.setdefault("compteurs", {})

    # --- compteurs journaliers -------------------------------------------
    def compteur(self, nom: str) -> int:
        c = self.d["compteurs"].get(nom, {})
        return c.get("n", 0) if c.get("jour") == jour_utc() else 0

    def incrementer(self, nom: str, n: int = 1) -> None:
        self.d["compteurs"][nom] = {"jour": jour_utc(), "n": self.compteur(nom) + n}

    # --- historique des publications -------------------------------------
    def ajouter_historique(self, genre: str, resume: str) -> None:
        self.d["historique"].append({"t": int(maintenant()), "genre": genre, "resume": resume[:200]})
        self.d["historique"] = self.d["historique"][-60:]

    def sauver(self) -> None:
        # Nettoyage : on oublie les matchs de plus de 2 jours.
        limite = maintenant() - 2 * 86400
        for fid in [k for k, m in self.d["matchs"].items() if m.get("coup_envoi", 0) < limite]:
            self.d["matchs"].pop(fid, None)
            self.d["evenements_vus"].pop(fid, None)
        self.d["actus_vues"] = self.d["actus_vues"][-800:]
        tmp = self.chemin.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.chemin)
