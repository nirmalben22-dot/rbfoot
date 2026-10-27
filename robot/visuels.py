"""Création des visuels (images 1080 x 1350) avec Pillow."""
from __future__ import annotations

import hashlib
import io
import logging
import re
import textwrap
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .base import DOSSIER_POLICES

log = logging.getLogger("robot")
L, H = 1080, 1350
_CACHE_LOGOS: dict[str, Image.Image | None] = {}


def _hex(c: str, defaut=(11, 18, 32)) -> tuple:
    c = (c or "").lstrip("#")
    try:
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return defaut


def _titre(taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(DOSSIER_POLICES / "Anton-Regular.ttf"), taille)


def _texte(taille: int, graisse: int = 700) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(DOSSIER_POLICES / "Montserrat-Variable.ttf"), taille)
    try:
        f.set_variation_by_axes([graisse])
    except Exception:  # noqa: BLE001
        pass
    return f


def _sans_emoji(s: str) -> str:
    # Les polices ne contiennent pas les émojis : on les retire des images.
    return re.sub(r"[\U0001F000-\U0001FFFF☀-➿️]", "", s or "").strip()


def _fond(couleur_fond, accent) -> Image.Image:
    img = Image.new("RGB", (L, H), couleur_fond)
    d = ImageDraw.Draw(img)
    # léger dégradé vertical
    for y in range(H):
        k = y / H
        c = tuple(int(couleur_fond[i] * (1 - 0.35 * k) + 0 * k) for i in range(3))
        d.line([(0, y), (L, y)], fill=c)
    # bandes diagonales discrètes
    calque = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    dc = ImageDraw.Draw(calque)
    for x in range(-H, L, 90):
        dc.line([(x, H), (x + H, 0)], fill=(255, 255, 255, 9), width=26)
    img.paste(calque, (0, 0), calque)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, L, 14], fill=accent)
    return img


def _centrer(d: ImageDraw.ImageDraw, y: int, texte: str, police, couleur, largeur_max=L - 120) -> int:
    texte = _sans_emoji(texte)
    while police.size > 20 and d.textlength(texte, font=police) > largeur_max:
        police = police.font_variant(size=police.size - 4)
    w = d.textlength(texte, font=police)
    d.text(((L - w) / 2, y), texte, font=police, fill=couleur)
    bbox = d.textbbox((0, 0), texte, font=police)
    return y + (bbox[3] - bbox[1])


def _logo(url: str | None, nom: str, taille: int, accent) -> Image.Image:
    img = None
    if url:
        if url not in _CACHE_LOGOS:
            try:
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                _CACHE_LOGOS[url] = Image.open(io.BytesIO(r.content)).convert("RGBA")
            except Exception as e:  # noqa: BLE001
                log.info("Logo indisponible (%s)", e)
                _CACHE_LOGOS[url] = None
        img = _CACHE_LOGOS[url]
    if img is not None:
        return ImageOps.contain(img, (taille, taille))
    # Pas de logo : pastille avec les initiales
    p = Image.new("RGBA", (taille, taille), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.ellipse([0, 0, taille - 1, taille - 1], fill=(255, 255, 255, 30), outline=accent, width=6)
    initiales = "".join(m[0] for m in re.findall(r"[A-Za-zÀ-ÿ]+", nom)[:3]).upper() or "?"
    f = _titre(int(taille * 0.38))
    w = d.textlength(initiales, font=f)
    bb = d.textbbox((0, 0), initiales, font=f)
    d.text(((taille - w) / 2, (taille - (bb[3] - bb[1])) / 2 - bb[1]), initiales, font=f, fill=(255, 255, 255))
    return p


def _pied(d, nom_page, accent):
    d.rectangle([0, H - 96, L, H], fill=(0, 0, 0))
    _centrer(d, H - 76, nom_page.upper(), _texte(38, 800), accent)


def _nom_fichier(dossier: Path, prefixe: str, *cles) -> Path:
    h = hashlib.sha1("|".join(map(str, cles)).encode()).hexdigest()[:10]
    return dossier / f"{prefixe}-{h}.png"


def carte_match(
    dossier: Path,
    page: dict,
    titre: str,
    competition: str,
    dom: dict,
    ext: dict,
    score: str,
    ligne1: str = "",
    ligne2: str = "",
    logos: bool = True,
    couleur_titre=None,
) -> Path:
    """dom/ext = {"nom": ..., "logo": url}"""
    fond = _hex(page.get("couleur_fond"))
    accent = _hex(page.get("couleur_accent"), (0, 230, 118))
    img = _fond(fond, accent)
    d = ImageDraw.Draw(img)

    _centrer(d, 80, competition.upper(), _texte(36, 600), (190, 200, 215))
    _centrer(d, 150, titre, _titre(170), couleur_titre or accent)

    # Logos + score
    t = 250
    y_logos = 470
    for equipe, cx in ((dom, L * 0.2), (ext, L * 0.8)):
        lg = _logo(equipe.get("logo") if logos else None, equipe.get("nom", ""), t, accent)
        img.paste(lg, (int(cx - lg.width / 2), int(y_logos + (t - lg.height) / 2)), lg)
        nom = _sans_emoji(equipe.get("nom", ""))
        f = _texte(38, 700)
        while d.textlength(nom, font=f) > 330 and f.size > 22:
            f = f.font_variant(size=f.size - 2)
        w = d.textlength(nom, font=f)
        d.text((cx - w / 2, y_logos + t + 30), nom, font=f, fill=(255, 255, 255))

    fs = _titre(150)
    w = d.textlength(score, font=fs)
    bb = d.textbbox((0, 0), score, font=fs)
    d.text(((L - w) / 2, y_logos + (t - (bb[3] - bb[1])) / 2 - bb[1]), score, font=fs, fill=(255, 255, 255))

    y = 900
    if ligne1:
        d.rounded_rectangle([60, y - 20, L - 60, y + 110], radius=24, fill=(255, 255, 255, 255))
        _centrer(d, y + 8, ligne1, _texte(56, 800), fond, largeur_max=L - 180)
        y += 150
    if ligne2:
        for morceau in textwrap.wrap(_sans_emoji(ligne2), 42)[:2]:
            y = _centrer(d, y, morceau, _texte(38, 500), (215, 222, 235)) + 16
    _pied(d, page.get("nom", ""), accent)

    chemin = _nom_fichier(dossier, "direct", titre, competition, dom.get("nom"), ext.get("nom"), score, ligne1, ligne2)
    img.save(chemin, "PNG", optimize=True)
    return chemin


ETIQUETTES = {
    "OFFICIEL": (0, 200, 83),
    "TRANSFERT": (41, 121, 255),
    "RUMEUR": (255, 171, 0),
    "BLESSURE": (255, 87, 34),
    "SCANDALE": (213, 0, 0),
    "INFO": (124, 77, 255),
}


def carte_actu(dossier: Path, page: dict, etiquette: str, titre: str, source: str) -> Path:
    fond = _hex(page.get("couleur_fond"))
    accent = _hex(page.get("couleur_accent"), (0, 230, 118))
    img = _fond(fond, accent)
    d = ImageDraw.Draw(img)

    etiquette = (etiquette or "INFO").upper()
    coul = ETIQUETTES.get(etiquette, ETIQUETTES["INFO"])
    fe = _texte(44, 800)
    w = d.textlength(etiquette, font=fe)
    d.rounded_rectangle([70, 110, 70 + w + 60, 190], radius=40, fill=coul)
    d.text((100, 122), etiquette, font=fe, fill=(255, 255, 255))

    titre = _sans_emoji(titre).upper()
    # Taille de police adaptée à la longueur du titre
    for taille in (150, 132, 116, 102, 90, 80, 70):
        f = _titre(taille)
        largeur_car = d.textlength("M", font=f) * 0.62
        lignes = textwrap.wrap(titre, max(8, int((L - 140) / largeur_car)))
        hauteur = len(lignes) * taille * 1.12
        if hauteur <= 820 and all(d.textlength(li, font=f) <= L - 140 for li in lignes):
            break
    y = 260
    for li in lignes:
        d.text((70, y), li, font=f, fill=(255, 255, 255))
        y += int(taille * 1.12)

    d.rectangle([70, H - 220, 190, H - 212], fill=accent)
    d.text((70, H - 195), f"Selon {_sans_emoji(source)}", font=_texte(36, 600), fill=(200, 208, 222))
    _pied(d, page.get("nom", ""), accent)

    chemin = _nom_fichier(dossier, "actu", etiquette, titre, source)
    img.save(chemin, "PNG", optimize=True)
    return chemin
