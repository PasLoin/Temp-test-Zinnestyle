import argparse
import colorsys
import random
import re
import sys
import time
from pathlib import Path

import yaml

SCHEMES = {
    "monochrome": [0.0, 0.0, 0.0, 0.0],
    "analogue": [0.0, 0.08, -0.08, 0.16],
    "complémentaire": [0.0, 0.5, 0.04, 0.54],
    "triade": [0.0, 1 / 3, 2 / 3, 0.04],
    "tétrade": [0.0, 0.25, 0.5, 0.75],
    "complémentaire décalée": [0.0, 0.42, 0.58, 0.08],
}

TONES = {
    "background": ((0.93, 0.97), (0.07, 0.12)),
    "area": ((0.80, 0.90), (0.14, 0.22)),
    "water": ((0.70, 0.82), (0.20, 0.30)),
    "building": ((0.76, 0.86), (0.20, 0.27)),
    "border": ((0.50, 0.62), (0.36, 0.46)),
    "mid": ((0.42, 0.58), (0.42, 0.58)),
    "major": ((0.62, 0.78), (0.42, 0.56)),
    "major_outline": ((0.30, 0.42), (0.22, 0.32)),
    "minor": ((0.98, 1.00), (0.28, 0.34)),
    "text": ((0.12, 0.22), (0.84, 0.92)),
}

MAJOR_ROADS = ["motorway", "trunk", "primary", "secondary", "tertiary"]
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")


class Painter:
    def __init__(self, rng):
        self.rng = rng
        self.dark = rng.random() < 0.3
        self.scheme = rng.choice(sorted(SCHEMES))
        base = rng.random()
        self.hues = [(base + o) % 1.0 for o in SCHEMES[self.scheme]]
        self.sat = rng.uniform(0.35, 0.85)

    def tone(self, name):
        lo, hi = TONES[name][1 if self.dark else 0]
        return self.rng.uniform(lo, hi)

    def hsl(self, h, s, l):
        r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
        return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))

    def pick(self, hue, sat_factor, tone, spread=0.03):
        h = self.hues[hue] + self.rng.uniform(-spread, spread)
        s = self.sat * sat_factor * self.rng.uniform(0.85, 1.15)
        return self.hsl(h, s, self.tone(tone))

    def rgba(self, hex_color, alpha):
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r},{g},{b},{alpha})"


ROLES = {
    "landuse": (0, 0.45, "area", 0.14),
    "green": (1, 0.65, "area", 0.05),
    "leisure": (1, 0.55, "area", 0.06),
    "water": (2, 0.70, "water", 0.03),
    "trees": (1, 0.60, "mid", 0.04),
    "buildings": (3, 0.30, "building", 0.03),
    "addresses": (3, 0.30, "text", 0.02),
    "pedestrian": (2, 0.55, "mid", 0.05),
    "cycleway": (1, 0.95, "mid", 0.02),
    "railway": (0, 0.06, "border", 0.02),
    "poi": (3, 0.80, "mid", 0.04),
    "street_furniture": (0, 0.15, "border", 0.02),
    "boundaries": (2, 0.80, "mid", 0.02),
}

SECONDARY_TONE = {
    "outline_color": "border",
    "border_color": "border",
    "color_private": "area",
    "tunnel_color": "area",
}


HALO_OUTLINES = {"pedestrian"}


def role_color(p, layer, field="color"):
    hue, sat, tone, spread = ROLES[layer]
    if field == "outline_color" and layer in HALO_OUTLINES:
        return p.pick(0, 0.05, "minor", 0.0)
    if field != "color":
        tone = SECONDARY_TONE.get(field, tone)
    return p.pick(hue, sat, tone, spread)


def generic_layer(p, layer, raw):
    out = {}
    if "color" in raw or layer in ROLES:
        out["color"] = role_color(p, layer)
    if "border_color" in raw:
        out["border_color"] = role_color(p, layer, "border_color")
    subtypes = {}
    for key, scfg in (raw.get("subtypes") or {}).items():
        scfg = scfg or {}
        s = {"color": role_color(p, layer)}
        for field in ("color_private", "outline_color", "tunnel_color"):
            if field in scfg:
                s[field] = role_color(p, layer, field)
        if isinstance(scfg.get("by_color"), dict):
            s["by_color"] = {k: role_color(p, layer) for k in scfg["by_color"]}
        subtypes[key] = s
    if subtypes:
        out["subtypes"] = subtypes
    return out


def roads_layer(p, raw, road_order):
    keys = list((raw.get("subtypes") or {}).keys()) or list(road_order)
    step = p.rng.choice([-1, 1]) * p.rng.uniform(0.02, 0.06)
    parents = {}
    subtypes = {}
    for key in keys:
        parent = key[:-5] if key.endswith("_link") else key
        if parent not in parents:
            if parent in MAJOR_ROADS:
                rank = MAJOR_ROADS.index(parent)
                h = p.hues[3] + step * rank
                s = p.sat * p.rng.uniform(0.8, 1.1) * (1 - rank * 0.12)
                lo, hi = TONES["major"][1 if p.dark else 0]
                l = lo + (hi - lo) * rank / (len(MAJOR_ROADS) - 1)
                parents[parent] = {
                    "color": p.hsl(h, s, l),
                    "outline_color": p.hsl(h, s, p.tone("major_outline")),
                }
            elif parent == "track":
                parents[parent] = {"color": p.pick(0, 0.5, "mid")}
            else:
                parents[parent] = {
                    "color": p.pick(0, 0.05, "minor"),
                    "outline_color": p.pick(0, 0.10, "border"),
                }
        subtypes[key] = dict(parents[parent])
    return {"subtypes": subtypes}


def public_transport_layer(p, raw):
    base = p.rng.random()
    keys = list((raw.get("subtypes") or {}).keys())
    subtypes = {}
    for i, key in enumerate(keys):
        h = base + i / max(len(keys), 1)
        subtypes[key] = {"color": p.hsl(h, p.rng.uniform(0.6, 0.9), p.tone("mid"))}
    out = {"color": p.pick(2, 0.9, "mid"), "subtypes": subtypes}
    if raw.get("swatch_colors") and subtypes:
        out["swatch_colors"] = [s["color"] for s in list(subtypes.values())[:2]]
    return out


def leisure_layer(p, raw):
    out = generic_layer(p, "leisure", raw)
    sports = raw.get("sports") or {}
    new_sports = {}
    if isinstance(sports.get("default"), dict):
        new_sports["default"] = {"color": p.pick(1, 0.7, "mid"), "outline_color": p.pick(1, 0.7, "border")}
    values = {}
    for key in (sports.get("values") or {}):
        hue = p.rng.randrange(4)
        values[key] = {"color": p.pick(hue, 0.7, "mid"), "outline_color": p.pick(hue, 0.7, "major_outline")}
    if values:
        new_sports["values"] = values
    if new_sports:
        out["sports"] = new_sports
    return out


def buildings_layer(p, raw):
    out = generic_layer(p, "buildings", raw)
    if isinstance(raw.get("materials"), dict):
        out["materials"] = {k: p.pick(3, 0.35, "building", 0.08) for k in raw["materials"]}
    return out


def indoor_layer(p, colors):
    subtypes = {}
    for key in colors:
        if key == "arrow":
            subtypes[key] = {"color": p.pick(0, 0.1, "background")}
        elif key == "wall":
            subtypes[key] = {"color": p.pick(3, 0.1, "border")}
        elif key == "elevator":
            subtypes[key] = {"color": p.pick(2, 0.9, "mid")}
        else:
            subtypes[key] = {"color": p.pick(p.rng.randrange(4), 0.5, "building", 0.06)}
    return {"subtypes": subtypes}


def palette_section(p, defaults, bg, text):
    out = {}
    for group, keys in defaults.items():
        values = {}
        for key in keys:
            if "halo" in key:
                values[key] = p.rgba(bg, 0.85)
            elif key == "bridge_casing":
                values[key] = text
            elif key in ("label",):
                values[key] = text
            elif key in ("stroke", "casing", "tunnel_core") or (group == "cycleway" and key == "oneway"):
                values[key] = bg
            elif key in ("oneway", "outline", "roof") or key.endswith("_outline"):
                values[key] = p.pick(0, 0.15, "border")
            elif key == "busway_center":
                values[key] = p.pick(2, 0.9, "mid")
            else:
                values[key] = p.pick(0, 0.10, "area")
        out[group] = values
    return out


def patterns_section(p, declared, www):
    out = {}
    for name, cfg in sorted(declared.items()):
        svg_path = www / cfg["file"]
        if not svg_path.is_file():
            continue
        found = sorted(set(HEX_RE.findall(svg_path.read_text(encoding="utf-8"))))
        replace = {}
        for color in found:
            if color.lower() in ("#fff", "#ffffff", "#000", "#000000"):
                continue
            replace[color] = p.pick(p.rng.randrange(4), 0.7, "border", 0.05)
        if replace:
            out[name] = {"replace": replace}
    return out


def typologie_variant(bm, config, seed, www):
    full = bm.with_default_subtypes(config)
    p = Painter(random.Random(f"{seed}/typologie"))
    bg = p.pick(0, 0.25, "background", 0.0)
    text = p.pick(0, 0.30, "text", 0.0)
    layers = {}
    for name, raw in (full.get("layers") or {}).items():
        raw = raw or {}
        if name == "roads":
            layers[name] = roads_layer(p, raw, bm.ROAD_ORDER)
        elif name == "public_transport":
            layers[name] = public_transport_layer(p, raw)
        elif name == "leisure":
            layers[name] = leisure_layer(p, raw)
        elif name == "buildings":
            layers[name] = buildings_layer(p, raw)
        elif name in ROLES:
            layers[name] = generic_layer(p, name, raw)
    layers["indoor"] = indoor_layer(p, bm.INDOOR_DEFAULTS["colors"])
    declared = {**bm.DEFAULT_PATTERNS, **(config.get("patterns") or {})}
    mode = "sombre" if p.dark else "clair"
    return {
        "variant": {"label": f"Typologie · {seed} · {p.scheme}, {mode}"},
        "map": {"background": bg, "font": text},
        "palette": palette_section(p, bm.PALETTE_DEFAULTS, bg, text),
        "patterns": patterns_section(p, declared, www),
        "layers": layers,
    }


BRUSSELS_BBOX = (4.24, 50.76, 4.48, 50.91)
MAX_ZOOM = 18
MAX_APPEAR = 17
FROZEN_ZOOMS = {("trees", "tree_row")}
FILL_PATTERN_LAYERS = {"landuse": "pattern", "green": "pattern_private"}


class Chaos:
    def __init__(self, rng):
        self.rng = rng

    def color(self):
        return "#{:06x}".format(self.rng.randrange(0x1000000))

    def rgba(self):
        r, g, b = (self.rng.randrange(256) for _ in range(3))
        return f"rgba({r},{g},{b},{round(self.rng.random(), 2)})"

    def opacity(self, lo=0.15):
        return round(self.rng.uniform(lo, 1.0), 2)

    def zoom_from(self, base):
        return self.rng.randint(base, max(base, min(MAX_APPEAR, base + 4)))

    def maybe(self, chance=0.5):
        return self.rng.random() < chance


def chaos_patterns(ch, declared, www):
    out = {}
    for name, cfg in sorted(declared.items()):
        svg_path = www / cfg["file"]
        entry = {"size": ch.rng.randint(8, 64)}
        if svg_path.is_file():
            found = sorted(set(HEX_RE.findall(svg_path.read_text(encoding="utf-8"))))
            if found:
                entry["replace"] = {color: ch.color() for color in found}
        out[name] = entry
    icons = sorted((www / "assets" / "icons").glob("*.svg"))
    for i in range(ch.rng.randint(2, 6) if icons else 0):
        icon = ch.rng.choice(icons)
        found = sorted(set(HEX_RE.findall(icon.read_text(encoding="utf-8"))))
        entry = {"file": icon.relative_to(www).as_posix(), "size": ch.rng.randint(10, 48)}
        if found:
            entry["replace"] = {color: ch.color() for color in found}
        out[f"chaos-{i + 1}"] = entry
    return out


def chaos_subtype(ch, bm, config, name, key, scfg, pattern_pool):
    s = {"color": ch.color(), "opacity": ch.opacity()}
    for field in ("color_private", "outline_color", "tunnel_color"):
        if field in scfg or ch.maybe(0.3):
            s[field] = ch.color()
    if isinstance(scfg.get("by_color"), dict):
        s["by_color"] = {k: ch.color() for k in scfg["by_color"]}
    if ch.maybe(0.3) and (name, key) not in FROZEN_ZOOMS:
        s["appear_at"] = ch.zoom_from(bm.base_appear_at(config, name, key))
    field = FILL_PATTERN_LAYERS.get(name)
    if field and pattern_pool:
        if ch.maybe(0.4):
            s[field] = ch.rng.choice(pattern_pool)
            if field == "pattern":
                s["pattern_at"] = ch.zoom_from(bm.base_appear_at(config, name, key))
        elif scfg.get(field):
            s[field] = None
        if isinstance(scfg.get("by_pattern"), dict):
            s["by_pattern"] = {k: ch.rng.choice(pattern_pool) for k in scfg["by_pattern"]}
    return s


def chaos_layer(ch, bm, config, name, raw, pattern_pool):
    appear = ch.zoom_from(bm.base_appear_at(config, name))
    out = {
        "color": ch.color(),
        "opacity": ch.opacity(),
        "visible": ch.maybe(0.85),
        "appear_at": appear,
        "labels_at": ch.rng.randint(appear, MAX_ZOOM),
    }
    if name == "buildings":
        out["border_color"] = ch.color()
        out["extrusion_3d"] = ch.maybe()
        out["osm_colour"] = ch.maybe()
        out["materials"] = {k: ch.color() for k in (raw.get("materials") or {})}
    elif "border_color" in raw:
        out["border_color"] = ch.color()
    if raw.get("swatch_colors"):
        out["swatch_colors"] = [ch.color() for _ in raw["swatch_colors"]]
    sports = raw.get("sports")
    if isinstance(sports, dict):
        new = {"markings_at": ch.rng.randint(14, MAX_ZOOM)}
        if isinstance(sports.get("default"), dict):
            new["default"] = {"color": ch.color(), "outline_color": ch.color(),
                              "length": ch.rng.randint(5, 200)}
        values = {k: {"color": ch.color(), "outline_color": ch.color(), "length": ch.rng.randint(5, 200)}
                  for k in (sports.get("values") or {})}
        if values:
            new["values"] = values
        out["sports"] = new
    subtypes = {}
    for key in sorted(bm.known_subtypes(config, name)):
        if name == "roads" and key not in bm.ROAD_DEFAULTS:
            continue
        scfg = ((raw.get("subtypes") or {}).get(key)) or {}
        subtypes[key] = chaos_subtype(ch, bm, config, name, key, scfg, pattern_pool)
    if subtypes:
        out["subtypes"] = subtypes
    return out


def chaos_indoor(ch, bm):
    base = bm.INDOOR_DEFAULTS
    return {
        "appear_at": ch.zoom_from(base["appear_at"]),
        "level_height": round(ch.rng.uniform(1.0, 9.0), 2),
        "slab": round(ch.rng.uniform(0.05, 1.5), 2),
        "volume_ratio": round(ch.rng.uniform(0.1, 1.0), 2),
        "floor_opacity": ch.opacity(),
        "volume_opacity": ch.opacity(),
        "subtypes": {k: {"color": ch.color()} for k in base["colors"]},
    }


def chaos_variant(bm, config, seed, www):
    ch = Chaos(random.Random(f"{seed}/chaos"))
    full = bm.with_default_subtypes(config)
    declared = {**bm.DEFAULT_PATTERNS, **(config.get("patterns") or {})}
    patterns = chaos_patterns(ch, declared, www)
    pattern_pool = sorted(patterns)
    palette = {group: {key: (ch.rgba() if "halo" in key else ch.color()) for key in keys}
               for group, keys in bm.PALETTE_DEFAULTS.items()}
    layers = {}
    for name in sorted(bm.variant_layer_names(config) - {"indoor"}):
        layers[name] = chaos_layer(ch, bm, config, name, (full.get("layers") or {}).get(name) or {},
                                   pattern_pool)
    layers["indoor"] = chaos_indoor(ch, bm)
    x0, y0, x1, y1 = BRUSSELS_BBOX
    return {
        "variant": {"label": f"Chaos · {seed}"},
        "map": {
            "background": ch.color(),
            "font": ch.color(),
            "center": [round(ch.rng.uniform(x0, x1), 5), round(ch.rng.uniform(y0, y1), 5)],
            "zoom": round(ch.rng.uniform(11, 17), 2),
        },
        "palette": palette,
        "patterns": patterns,
        "layers": layers,
    }


GENERATORS = {"typologie": typologie_variant, "chaos": chaos_variant}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--zinneke", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--seed", default="")
    args = parser.parse_args()
    zinneke = Path(args.zinneke).resolve()
    sys.path.insert(0, str(zinneke))
    import build_map as bm

    seed = args.seed or str(int(time.time()))
    config = yaml.safe_load((zinneke / "map.config.yaml").read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for vid, generate in GENERATORS.items():
        variant = generate(bm, config, seed, zinneke / "www")
        errors = bm.variant_errors(config, variant, vid)
        if errors:
            raise SystemExit(f"{vid} invalide :\n" + "\n".join(f"  - {e}" for e in errors))
        out = out_dir / f"{vid}.yaml"
        out.write_text(yaml.safe_dump(variant, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"✓  {out}  ({variant['variant']['label']})")


if __name__ == "__main__":
    main()
