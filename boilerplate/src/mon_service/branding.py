# -*- coding: utf-8 -*-
"""Branding profiles for the MCP starter-kit admin UI.

Selected via MCP_BRAND:
- ct   = Cloud Temple
- dgy  = Dragonfly
- isec = Intrinsec
"""

from copy import deepcopy
from typing import Optional

BRANDS = {
    "ct": {
        "code": "ct",
        "company_name": "Cloud Temple",
        "app_title": "Admin Console",
        "logo": "/admin/static/img/logo-ct.svg",
        "colors": {
            "accent": "#41a890",
            "accent_hover": "#369b82",
            "bg": "#0f0f23",
            "surface": "#1a1a2e",
        },
    },
    "dgy": {
        "code": "dgy",
        "company_name": "Dragonfly",
        "app_title": "Admin Console",
        "logo": "/admin/static/img/logo-dgy.svg",
        "colors": {
            "accent": "#ff5a00",
            "accent_hover": "#e64f00",
            "bg": "#0f0f23",
            "surface": "#1a1a2e",
        },
    },
    "isec": {
        "code": "isec",
        "company_name": "Intrinsec",
        "app_title": "Admin Console",
        "logo": "/admin/static/img/logo-isec.svg",
        "colors": {
            "accent": "#c91517",
            "accent_hover": "#b51214",
            "bg": "#0f0f23",
            "surface": "#1a1a2e",
        },
    },
}

DEFAULT_BRAND = "ct"


def get_brand_profile(code: Optional[str]) -> dict:
    """Return a safe copy of the selected brand profile.

    Unknown values fall back to Cloud Temple to keep the starter-kit robust.
    """
    normalized = (code or DEFAULT_BRAND).strip().lower()
    profile = BRANDS.get(normalized, BRANDS[DEFAULT_BRAND])
    return deepcopy(profile)
