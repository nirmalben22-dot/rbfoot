"""Point d'entrée : python -m robot.main --mode run|check"""
from __future__ import annotations

import argparse
import logging
import sys

from .actualites import Actualites
from .base import DOSSIER_SORTIE, Etat, charger_config, configurer_journal, secret
from .direct import Direct
from .services import ApiFootball, Gemini, Publieur

log = logging.getLogger("robot")


class CompteurErreurs(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.n = 0

    def emit(self, record):
        self.n += 1


def verifier(cfg: dict) -> int:
    """Vérifie chaque clé et affiche un diagnostic clair."""
    ok = True
    print("\n=========== VÉRIFICATION DES CLÉS ===========\n")

    cle = secret("API_FOOTBALL_KEY")
    if not cle:
        print("❌ API_FOOTBALL_KEY manquante (Secrets GitHub).")
        ok = False
    else:
        api = ApiFootball(cle)
        try:
            st = api.statut()
            abo = st.get("subscription", {})
            req = st.get("requests", {})
            print(f"✅ API-Football : forfait « {abo.get('plan')} », "
                  f"{req.get('current')}/{req.get('limit_day')} appels utilisés aujourd'hui.")
            try:
                ids = {int(x["id"]) for x in cfg.get("direct", {}).get("competitions", [])}
                from .base import jour_utc
                n = len(api.matchs_du_jour(jour_utc(), ids))
                print(f"✅ API-Football : accès aux matchs du jour OK ({n} match(s) aujourd'hui dans vos compétitions).")
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  API-Football : clé valide, mais les matchs du jour sont refusés :\n    {e}")
                ok = False
        except Exception as e:  # noqa: BLE001
            print(f"❌ API-Football : {e}")
            ok = False

    cle = secret("GEMINI_API_KEY")
    if not cle:
        print("❌ GEMINI_API_KEY manquante.")
        ok = False
    else:
        try:
            g = Gemini(cle, cfg.get("gemini", {}).get("modeles", []))
            rep = g.json('Réponds exactement {"ok": true}')
            print(f"✅ Gemini : répond correctement ({rep}).")
        except Exception as e:  # noqa: BLE001
            print(f"❌ Gemini : {e}")
            ok = False

    pid, jeton = secret("FB_PAGE_ID"), secret("FB_PAGE_TOKEN")
    if not pid or not jeton:
        print("❌ FB_PAGE_ID ou FB_PAGE_TOKEN manquant.")
        ok = False
    else:
        try:
            p = Publieur(pid, jeton, cfg.get("facebook", {}).get("version_api", "v25.0"), True, DOSSIER_SORTIE)
            print(f"✅ Facebook : connecté à la page « {p.verifier()} ».")
        except Exception as e:  # noqa: BLE001
            print(f"❌ Facebook : {e}")
            ok = False

    import time as _t
    import feedparser
    import requests
    from .actualites import liste_flux
    from .services import UA
    print("\n--- Sources d'actualités (articles des dernières 24 h) ---")
    for flux in liste_flux(cfg.get("actualites", {})):
        try:
            r = requests.get(flux["url"], headers=UA, timeout=20)
            r.raise_for_status()
            n = len(feedparser.parse(r.content).entries)
            print(f"{'✅' if n else '⚠️ '} {flux['nom']} : {n} article(s).")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️  {flux['nom']} illisible ({e}). Retirez-le de config.yaml ou corrigez-le.")
        if flux.get("genre") == "google":
            _t.sleep(1)

    print("\n" + ("🎉 Tout est prêt." if ok else "Corrigez les points marqués ❌ ou ⚠️ puis relancez la vérification.") + "\n")
    return 0 if ok else 1


def lancer(cfg: dict, test: bool) -> int:
    compteur = CompteurErreurs()
    logging.getLogger().addHandler(compteur)
    etat = Etat()
    pub = Publieur(
        secret("FB_PAGE_ID"), secret("FB_PAGE_TOKEN"),
        cfg.get("facebook", {}).get("version_api", "v25.0"), test, DOSSIER_SORTIE,
    )
    if test:
        log.info("MODE TEST : rien n'est publié, les visuels sont enregistrés dans « sortie ».")
    elif not (pub.page_id and pub.jeton):
        log.warning("Clés Facebook absentes : le robot attend que vous ajoutiez les Secrets.")
        return 0

    if cfg.get("direct", {}).get("actif", True):
        cle = secret("API_FOOTBALL_KEY")
        if not cle:
            log.warning("Direct désactivé : API_FOOTBALL_KEY absente.")
        else:
            try:
                Direct(cfg, etat, ApiFootball(cle, etat), pub).lancer()
            except Exception as e:  # noqa: BLE001
                log.error("Direct : %s", e)
            etat.sauver()

    if cfg.get("actualites", {}).get("actif", True):
        cle = secret("GEMINI_API_KEY")
        if not cle:
            log.warning("Actualités désactivées : GEMINI_API_KEY absente.")
        else:
            try:
                Actualites(cfg, etat, Gemini(cle, cfg.get("gemini", {}).get("modeles", [])), pub).lancer()
            except Exception as e:  # noqa: BLE001
                log.error("Actualités : %s", e)
            etat.sauver()

    log.info("Terminé : %d publication(s)%s, %d erreur(s).", pub.nb, " (test)" if test else "", compteur.n)
    return 1 if compteur.n else 0


def principal() -> None:
    configurer_journal()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["run", "check"], default="run")
    args = ap.parse_args()
    cfg = charger_config()
    if args.mode == "check":
        sys.exit(verifier(cfg))
    test = secret("DRY_RUN").lower() not in ("false", "0", "non", "no")
    sys.exit(lancer(cfg, test))


if __name__ == "__main__":
    principal()
