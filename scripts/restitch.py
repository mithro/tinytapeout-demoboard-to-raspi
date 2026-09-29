#!/usr/bin/env python3
"""Put a stitching via into every front-copper ground island left stranded.

make_direct.py takes out the ground stitching vias that would have sat
under the socket's SMT pads, and the ones in the 10 mm that is cut off.
Some of the pour's islands on the front had no other way down to the pour
on the back, and a fill island with a pad in it but no route to the rest
of its net is a missing connection.

Run after ``scripts/kicad10.sh refill`` has written the fill: this reads
the filled polygons on both faces, joins them into clusters through the
vias and through-hole ground pads they reach, and for every cluster that
does not reach the main pour puts one plain via where one of its polygons
overlaps the main pour of the other face, at the point furthest inside
both, so the via lands in clear copper on both faces.  Then refill again.

Run: python3 scripts/restitch.py
"""

from __future__ import annotations

import math
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_direct import block_end, top_level_blocks  # noqa: E402

PCB = Path(__file__).resolve().parent.parent / "TTDB_2_Raspi.kicad_pcb"
NET = "GND"
VIA_DIA, VIA_DRILL = 0.8, 0.4
#: How far a via's centre has to be from the edge of the pour, on both
#: faces: its own radius and a little.  The pour's edge already stands the
#: board's clearance off every other net's copper and off the board edge,
#: so a via whose rim reaches the pour's edge has that clearance too.
MARGIN = VIA_DIA / 2 + 0.05
GRID = 0.1
#: A through-hole pad or via joined to the pour by thermal spokes sits in a
#: hole in the fill, so its centre is not inside the polygon: it counts as
#: in the island when the island's edge comes this close to it, the pad's
#: radius plus the thermal gap and a little.
TOUCH = 0.85 + 0.5 + 0.15


def polygons(text: str, layer: str) -> list[list[tuple[float, float]]]:
    out = []
    for a, b in top_level_blocks(text, "zone"):
        zone = text[a:b]
        if f'(net "{NET}")' not in zone:
            continue
        i = 0
        while True:
            j = zone.find("\n\t\t(filled_polygon", i)
            if j < 0:
                break
            k = block_end(zone, j + 3)
            fp = zone[j:k]
            if f'(layer "{layer}")' in fp:
                out.append([(float(x), float(y)) for x, y in
                            re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", fp)])
            i = k
    return out


def inside(poly, x, y) -> bool:
    ok = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            ok = not ok
    return ok


def edge_distance(poly, x, y) -> float:
    best = math.inf
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def area(poly) -> float:
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                   - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def through_points(text: str) -> list[tuple[float, float]]:
    """Every via and through-hole pad on the net: where the front meets the back."""
    pts = []
    for a, b in top_level_blocks(text, "via"):
        blk = text[a:b]
        if f'(net "{NET}")' in blk:
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)\)", blk)
            pts.append((float(at[1]), float(at[2])))
    for a, b in top_level_blocks(text, "footprint"):
        blk = text[a:b]
        fat = re.search(r"\n\t\t\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", blk)
        fx, fy, rot = float(fat[1]), float(fat[2]), math.radians(float(fat[3] or 0))
        for m in re.finditer(r'\n\t\t\(pad "[^"]*" thru_hole', blk):
            pb = blk[m.start() + 3:block_end(blk, m.start() + 3)]
            if f'(net "{NET}")' not in pb:
                continue
            at = re.search(r"\(at ([-\d.]+) ([-\d.]+)", pb)
            px, py = float(at[1]), float(at[2])
            pts.append((fx + px * math.cos(rot) + py * math.sin(rot),
                        fy - px * math.sin(rot) + py * math.cos(rot)))
    return pts


def touches(poly, x, y) -> bool:
    return inside(poly, x, y) or edge_distance(poly, x, y) <= TOUCH


def via_spot(a, b):
    """The point furthest inside both polygons, and how far inside it is."""
    xs = [p[0] for p in a]
    ys = [p[1] for p in a]
    best, best_d = None, 0.0
    x = min(xs)
    while x <= max(xs):
        y = min(ys)
        while y <= max(ys):
            if inside(a, x, y) and inside(b, x, y):
                d = min(edge_distance(a, x, y), edge_distance(b, x, y))
                if d > best_d:
                    best, best_d = (x, y), d
            y += GRID
        x += GRID
    return best, best_d


def main() -> None:
    text = PCB.read_text()
    front = polygons(text, "F.Cu")
    back = polygons(text, "B.Cu")
    if not front or not back:
        raise SystemExit("no fill in the board: run scripts/kicad10.sh refill first")
    polys = [("F.Cu", p) for p in front] + [("B.Cu", p) for p in back]
    main_back = max(range(len(polys)), key=lambda i: area(polys[i][1])
                    if polys[i][0] == "B.Cu" else 0)
    main_front = max(range(len(polys)), key=lambda i: area(polys[i][1])
                     if polys[i][0] == "F.Cu" else 0)
    through = through_points(text)

    # Which polygons each through point joins; polygons joined by a point
    # are one cluster, and the net is whole when there is one cluster.
    parent = list(range(len(polys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for x, y in through:
        joined = [i for i, (_, p) in enumerate(polys) if touches(p, x, y)]
        for i in joined[1:]:
            parent[find(i)] = find(joined[0])
    root = find(main_back)
    if find(main_front) != root:
        raise SystemExit("the main front and back pours are not joined")

    clusters: dict[int, list[int]] = {}
    for i in range(len(polys)):
        r = find(i)
        if r != root:
            clusters.setdefault(r, []).append(i)
    main_members = [i for i in range(len(polys)) if find(i) == root]
    added = []
    for members in clusters.values():
        # A via from one of the cluster's polygons into any polygon of the
        # main cluster on the other face, wherever the two overlap most
        # comfortably.
        best, best_d = None, 0.0
        for i in members:
            for j in main_members:
                if polys[j][0] == polys[i][0]:
                    continue
                spot, d = via_spot(polys[i][1], polys[j][1])
                if spot is not None and d > best_d:
                    best, best_d = spot, d
        what = ", ".join(f"{polys[i][0]} {area(polys[i][1]):.1f} mm2 at "
                         f"({min(p[0] for p in polys[i][1]):.1f}, "
                         f"{min(p[1] for p in polys[i][1]):.1f})" for i in members)
        if best is None or best_d < MARGIN:
            raise SystemExit(f"no room for a via to join the cluster [{what}]: "
                             f"best {best_d:.2f} mm")
        added.append((best, best_d, what))
    vias = "".join(
        f"\n\t(via\n\t\t(at {x:.3f} {y:.3f})\n\t\t(size {VIA_DIA})\n\t\t(drill {VIA_DRILL})"
        f'\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "{NET}")\n\t\t(uuid "{uuid.uuid4()}")\n\t)'
        for (x, y), _, _ in added)
    a, _ = top_level_blocks(text, "segment")[0]
    PCB.write_text(text[:a] + vias + text[a:])
    for (x, y), d, what in added:
        print(f"via at ({x:.2f}, {y:.2f}), {d:.2f} mm inside both pours, "
              f"joining [{what}]")
    print(f"{len(added)} via(s) added; refill the pour again")


if __name__ == "__main__":
    main()
