#!/usr/bin/env python3
"""Turn the Raspmod into its direct-on-GPIO variant.

Reads TTDB_2_Raspi.kicad_pcb and TTDB_2_Raspi.kicad_sch as Pat Deegan
published them (commit 0dae4bf) and rewrites them in place.  Every edit is
string surgery on the s-expressions, so the files stay in the KiCad 10
format they were written in and every pad that is kept stays exactly where
it was, with its number and its net; scripts/check_direct.py proves that
under KiCad's own Python.

What changes, and why:

* J1, the 2x20 box header the ribbon plugged into, becomes a low-profile
  SMT socket on the UNDERSIDE -- Adafruit 2187, Kaweei CS25582-40G-M36-0A,
  3.8 mm tall -- so the board sits straight on a Raspberry Pi's 40-pin
  header.  The socket's own drawing has the pins pass through the PCB, so
  the forty plated holes stay as they are and forty SMT pads join them.
* J2, J3, J4, the straight 2x6 plugs on the underside that went into a
  demoboard's Pmod hosts while the board stood on edge, become right-angle
  2x6 headers on the COMPONENT side, pins out past the edge, so the board
  lies flat beside the demoboard (or a Digilent Arty) and plugs in
  sideways.  Same pads, same numbers: the right-angle footprint is the
  Tiny Tapeout library's, mirrored, because the library's numbering would
  put pin 1 at the other end of the row.
* The board is 10 mm shorter at the pin-40 end.  Sat on the Pi that end
  reached over the Pi's Ethernet or USB stack, which is 13.5 to 16 mm
  tall; the cut clears every Pi model's.  J10 (the auxiliary socket) and
  SW1 (the user button, with R1, R2 and C1) lived there; the README calls
  everything but the headers optional, and they go.  J9, the clock/reset
  socket on the underside, goes too: it dropped onto the demoboard's pins
  from above and cannot reach them from beside it.  J8 on top stays, for
  a flying lead.
* Fits a Raspberry Pi 5 or 3B.  Not a 3B+ or 4B: their PoE header stands
  8.5 mm tall 0.4 mm from pin 39's pad, under a board that sits at
  2.54 + 3.8 = 6.34 mm.

Run: python3 scripts/make_direct.py
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PCB = ROOT / "TTDB_2_Raspi.kicad_pcb"
SCH = ROOT / "TTDB_2_Raspi.kicad_sch"
LIB = ROOT / "RaspmodDirect.pretty"

#: Board frame: KiCad x = board x + OX, KiCad y = OY - board y.  The board's
#: lower-left corner in the file, read off its Edge.Cuts.
OX, OY = 68.6, 73.1
#: Where the new left edge goes, in board millimetres from the old one.
CUT_X = 10.0
CORNER_R = 2.0

SOCKET = "RaspmodDirect:PinSocket_2x20_P2.54mm_SMD_CS25582_Under"
PLUG = "RaspmodDirect:PinHeader_2x06_P2.54mm_PMODPeriph2B_TopRA_Mirror"
OLD_SOCKET = "Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical"
OLD_PLUG = "TinyTapeout:PinHeader_2x06_P2.54mm_PMODPeriph2B_UNDERVERT"

#: The Kaweei CS25582 layout: SMT pads 1.02 x 2.10 on the 2.54 pitch, the
#: two rows of them 8.3 mm apart centre to centre, so 4.15 either side of
#: the header's centre line, 3.10 to 5.20 out.  Here they are the middle
#: 1.50 of that, 3.43 to 4.93, on both rows: the header's centre line is
#: 5.53 from this board's top edge, so the full pad would stop 0.33 from
#: the edge where the board's rule wants 0.50; and between the holes and
#: the pads there has to be room for the ground pour to thread along the
#: header, or the ground pins of the outer row are boxed in on the back
#: by the signal pads either side of them.  0.31 mm of pour is what is
#: left there, above the 0.25 the pour's own rule allows.  The leg's foot
#: lands in the middle of its pad as it would on the full one.
SMT_ROW = 4.18
SMT_PAD = (1.5, 1.02)
#: The header's centre line, in board millimetres from the lower edge.
HEADER_Y = (27.04 + 24.50) / 2
#: Where those pads lie, across the board: the ground stitching vias in
#: these bands would sit under the socket's pads, and go.
SMT_BANDS = [(HEADER_Y + SMT_ROW - SMT_PAD[0] / 2 - 0.5,
              HEADER_Y + SMT_ROW + SMT_PAD[0] / 2 + 0.5),
             (HEADER_Y - SMT_ROW - SMT_PAD[0] / 2 - 0.5,
              HEADER_Y - SMT_ROW + SMT_PAD[0] / 2 + 0.5)]
#: Along the board, the socket's pads run from pin 39/40 to pin 1/2.
SMT_X = (19.02 - 1.0, 67.28 + 1.0)

REMOVED_REFS = {"J9", "J10", "SW1", "R1", "R2", "C1"}
#: Marked not fitted on the schematic: those, and the fiducial that was in
#: the cut.
UNFITTED_REFS = REMOVED_REFS | {"FID3"}


def new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# s-expression surgery
# ---------------------------------------------------------------------------

def block_end(text: str, start: int) -> int:
    """Index just past the balanced expression that opens at text[start]."""
    assert text[start] == "("
    depth = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while text[i] != '"':
                if text[i] == "\\":
                    i += 1
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise SystemExit("unbalanced expression")


def top_level_blocks(text: str, head: str) -> list[tuple[int, int]]:
    """(start, end) of every ``\\n\\t(head`` block at depth one."""
    out = []
    key = f"\n\t({head}"
    i = 0
    while True:
        i = text.find(key, i)
        if i < 0:
            return out
        start = i + 2
        # Only whole tokens: "(net" must not match "(net_class".
        after = text[start + 1 + len(head)]
        if after not in " \n\t)":
            i = start
            continue
        end = block_end(text, start)
        out.append((i, end))
        i = end


def reference_of(block: str) -> str:
    m = re.search(r'\(property "Reference" "([^"]*)"', block)
    return m.group(1) if m else ""


def fp_origin(block: str) -> tuple[float, float]:
    m = re.search(r'\n\t\t\(at ([-\d.]+) ([-\d.]+)', block)
    return float(m.group(1)), float(m.group(2))


def to_board(kx: float, ky: float) -> tuple[float, float]:
    return kx - OX, OY - ky


def replace_once(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SystemExit(f"expected exactly one {old!r}, found {n}")
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# the outline
# ---------------------------------------------------------------------------

def cut_outline(text: str) -> str:
    """Move the left edge in by CUT_X, keeping the R2 corners."""
    x0 = OX + CUT_X
    r = CORNER_R
    c = r * (1 - 2 ** -0.5)          # where a quarter arc's midpoint sits
    yt, yb = OY - 31.3, OY           # the outline's top and bottom, KiCad y
    edits = [
        ("(gr_line\n\t\t(start 68.6 71.1)\n\t\t(end 68.6 43.8)",
         f"(gr_line\n\t\t(start {x0:g} 71.1)\n\t\t(end {x0:g} 43.8)"),
        ("(gr_arc\n\t\t(start 68.6 43.8)\n\t\t(mid 69.185786 42.385786)\n\t\t(end 70.6 41.8)",
         f"(gr_arc\n\t\t(start {x0:g} 43.8)\n\t\t(mid {x0 + c:.6f} {yt + c:.6f})\n\t\t(end {x0 + r:g} 41.8)"),
        ("(gr_arc\n\t\t(start 70.6 73.1)\n\t\t(mid 69.185786 72.514214)\n\t\t(end 68.6 71.1)",
         f"(gr_arc\n\t\t(start {x0 + r:g} 73.1)\n\t\t(mid {x0 + c:.6f} {yb - c:.6f})\n\t\t(end {x0:g} 71.1)"),
        ("(gr_line\n\t\t(start 70.6 41.8)\n\t\t(end 138.9 41.8)",
         f"(gr_line\n\t\t(start {x0 + r:g} 41.8)\n\t\t(end 138.9 41.8)"),
        ("(gr_line\n\t\t(start 138.9 73.1)\n\t\t(end 70.6 73.1)",
         f"(gr_line\n\t\t(start 138.9 73.1)\n\t\t(end {x0 + r:g} 73.1)"),
    ]
    for old, new in edits:
        text = replace_once(text, old, new)
    return text


# ---------------------------------------------------------------------------
# what goes: parts, silk, copper in the cut, and whatever that leaves dangling
# ---------------------------------------------------------------------------

def remove_blocks(text: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + text[b:]
    return text


def drop_footprints(text: str) -> tuple[str, list[str]]:
    gone = []
    spans = []
    for a, b in top_level_blocks(text, "footprint"):
        block = text[a:b]
        ref = reference_of(block)
        bx, by = to_board(*fp_origin(block))
        # Anything whose origin is in the cut, or within a millimetre of
        # the new edge: two GND stitching vias sat 0.9 mm from it.
        if ref in REMOVED_REFS or bx < CUT_X + 1.0:
            gone.append(f"{ref or block.split(chr(34))[1]} at x={bx:.2f}")
            spans.append((a, b))
        # And the ground stitching vias under the socket's SMT pads, or in
        # the way of the 3V3 rail's new run along the top: a via 0.4 mm
        # from copper on another net is a short.
        elif "StitchingVia" in block and (
                (SMT_X[0] < bx < SMT_X[1] and any(lo < by < hi for lo, hi in SMT_BANDS))
                or (12.0 < bx < 68.0 and abs(by - RAIL_Y) < 1.0)):
            gone.append(f"{ref} at ({bx:.2f},{by:.2f}), under the socket's pads or the rail")
            spans.append((a, b))
    return remove_blocks(text, spans), gone


#: Where the 3V3 rail runs now, in board millimetres from the lower edge.
#: It ran on the back at 29.3, under what is now the socket's outer row of
#: pads.  On the front, 0.75 in from the edge, it leaves a strip of ground
#: pour between itself and the pin row, and that strip is the only way out
#: for the ground pins of the outer row: on the back each of them is boxed
#: in by the signal pads either side, the pads' own links to their holes
#: and the edge.  The strip has to be whole from end to end, which is why
#: the rail is fed from pin 1 alone and pin 17 is left unconnected: a stub
#: from pin 17 up to the rail would cut the strip in two and box pin 9 in.
#: The Pi's two 3V3 pins are one rail, and the socket's contact is rated
#: 3 A against three Pmods' 100 mA each.
RAIL_Y = 30.35


def rebuild_vdd_rail(text: str) -> str:
    """The 3V3 rail along the top edge, redrawn on the front copper.

    Pin 1 up to the rail, along to the left end and down to the front
    track that already runs to the power jumper J11.  The old rail's four
    back segments, the two front stubs that met it at pin 17, and its two
    vias all go.
    """
    spans = []
    for a, b in top_level_blocks(text, "segment"):
        block = text[a:b]
        if '(net "VDD")' not in block:
            continue
        pts = [to_board(float(x), float(y)) for x, y in
               re.findall(r'\((?:start|end) ([-\d.]+) ([-\d.]+)\)', block)]
        # The back rail, and the front stubs above pin 17.
        if min(p[1] for p in pts) >= 27.0:
            spans.append((a, b))
    if len(spans) != 6:
        raise SystemExit(f"the VDD rail is {len(spans)} segments, not the 6 expected")
    text = remove_blocks(text, spans)
    spans = []
    for a, b in top_level_blocks(text, "via"):
        if '(net "VDD")' in text[a:b]:
            spans.append((a, b))
    if len(spans) != 2:
        raise SystemExit(f"{len(spans)} VDD vias, not the 2 expected")
    text = remove_blocks(text, spans)

    pin1 = (67.28, 27.04)
    rise = RAIL_Y - pin1[1]                        # a 45 degree corner
    path = [
        (pin1, (pin1[0] - rise, RAIL_Y)),
        ((pin1[0] - rise, RAIL_Y), (13.30, RAIL_Y)),
        ((13.30, RAIL_Y), (12.50, RAIL_Y - 0.80)),
        ((12.50, RAIL_Y - 0.80), (12.50, 27.90)),
    ]
    rail = ""
    for (x0, y0), (x1, y1) in path:
        rail += (f"\n\t(segment\n\t\t(start {x0 + OX:.3f} {OY - y0:.3f})"
                 f"\n\t\t(end {x1 + OX:.3f} {OY - y1:.3f})\n\t\t(width 0.4)"
                 f'\n\t\t(layer "F.Cu")\n\t\t(net "VDD")'
                 f'\n\t\t(uuid "{new_uuid()}")\n\t)')
    a, _ = top_level_blocks(text, "segment")[0]
    return text[:a] + rail + text[a:]


def header_links(text: str) -> str:
    """A back-copper track from each of J1's holes to its SMT pad.

    The two pads share a number and the footprint declares them jumpered,
    which keeps DRC from calling them a short; but KiCad's connectivity
    does not carry a net across that declaration, so a pour reaching only
    the SMT pad counted as reaching nothing.  Forty 0.5 mm tracks, each
    straight across the 2.73 mm between hole and pad in its own column,
    make the connection copper rather than a flag.
    """
    links = ""
    for n in range(1, 41):
        if n == UNCONNECTED_PIN:
            continue
        bx = 67.28 - ((n - 1) // 2) * 2.54
        by = 27.04 if n % 2 else 24.50
        to = by + (SMT_ROW - 1.27) if n % 2 else by - (SMT_ROW - 1.27)
        net = _j1_net(text, n)
        links += (f"\n\t(segment\n\t\t(start {bx + OX:.3f} {OY - by:.3f})"
                  f"\n\t\t(end {bx + OX:.3f} {OY - to:.3f})\n\t\t(width 0.5)"
                  f'\n\t\t(layer "B.Cu")\n\t\t(net "{net}")'
                  f'\n\t\t(uuid "{new_uuid()}")\n\t)')
    a, _ = top_level_blocks(text, "segment")[0]
    return text[:a] + links + text[a:]


#: J1's second 3V3 pin, left off the rail: see RAIL_Y.
UNCONNECTED_PIN = 17


#: What KiCad calls the net of a pin marked no-connect on the schematic,
#: which is what the board's pads have to say for the two to agree.
UNCONNECTED_NET = f"unconnected-(J1-3V3-Pad{UNCONNECTED_PIN})"


def release_pin17(text: str) -> str:
    """Take J1's pin 17, hole and SMT pad both, off the VDD net."""
    n = 0
    for a, b in top_level_blocks(text, "footprint"):
        block = text[a:b]
        if reference_of(block) != "J1":
            continue
        for m in reversed(list(re.finditer(rf'\n\t\t\(pad "{UNCONNECTED_PIN}" ', block))):
            k = block_end(block, m.start() + 3)
            pad = block[m.start():k]
            pad2 = pad.replace('(net "VDD")', f'(net "{UNCONNECTED_NET}")')
            if pad2 == pad:
                raise SystemExit("J1 pin 17 is not on VDD")
            block = block[:m.start()] + pad2 + block[k:]
            n += 1
        text = text[:a] + block + text[b:]
    if n != 2:
        raise SystemExit(f"released {n} pads of pin 17, not 2")
    return text


def _j1_net(text: str, n: int) -> str:
    for a, b in top_level_blocks(text, "footprint"):
        block = text[a:b]
        if reference_of(block) != "J1":
            continue
        m = re.search(rf'\n\t\t\(pad "{n}" thru_hole(?:.|\n)*?\(net "([^"]*)"\)', block)
        return m.group(1)
    raise SystemExit("no J1")


def ground_stubs(text: str) -> str:
    """Pads 6 and 30 of J1 are GND and get a stub each into the pour.

    Both sit between front-copper tracks 0.45 mm away, closer than the
    pour's 0.5 mm clearance, so the pour never reached them on the front;
    on the back they reached it outboard of the row, where the socket's
    pads now are.  A 0.4 mm track on the back from each pad, down through
    its own SMT pad and out below it, meets the pour there.
    """
    stubs = ""
    for bx in (62.20, 31.72):
        kx = bx + OX
        stubs += (f"\n\t(segment\n\t\t(start {kx:g} {OY - 24.5:g})"
                  f"\n\t\t(end {kx:g} {OY - 20.0:g})\n\t\t(width 0.4)"
                  f'\n\t\t(layer "B.Cu")\n\t\t(net "GND")'
                  f'\n\t\t(uuid "{new_uuid()}")\n\t)')
    a, _ = top_level_blocks(text, "segment")[0]
    return text[:a] + stubs + text[a:]


def drop_graphics(text: str) -> tuple[str, list[str]]:
    gone = []
    spans = []
    for head in ("gr_text", "gr_line", "gr_rect", "gr_arc", "gr_circle",
                 "gr_poly"):
        for a, b in top_level_blocks(text, head):
            block = text[a:b]
            if '(layer "Edge.Cuts")' in block:
                continue
            pts = [to_board(float(x), float(y)) for x, y in
                   re.findall(r'\((?:start|end|at|mid) ([-\d.]+) ([-\d.]+)',
                              block)]
            label = re.search(r'\(gr_text "((?:[^"\\]|\\.)*)"', block)
            # The HOST label was 0.3 mm inside the cut, and anywhere else
            # it lands on J5's own silk; the CLK and RST labels at the
            # plug edge named J9's two pins.
            orphan = label and (label.group(1) == "HOST" or (
                label.group(1) in ("CLK", "~{RST}") and pts[0][1] < 8.0))
            if (pts and max(p[0] for p in pts) < CUT_X) or orphan:
                gone.append(f"{head} {label.group(1) if label else ''} "
                            f"at {pts[0][0]:.1f},{pts[0][1]:.1f}")
                spans.append((a, b))
    text = remove_blocks(text, spans)
    # The two long silk boxes round the plug row start 0.7 mm into the cut
    # and run the whole width: pull their left side clear of the new
    # corner's radius.
    old = "(gr_rect\n\t\t(start 77.9 65.4)"
    if text.count(old) != 2:
        raise SystemExit("the plug-row silk boxes are not where expected")
    text = text.replace(old, f"(gr_rect\n\t\t(start {OX + CUT_X + CORNER_R + 0.6:g} 65.4)")
    # The power jumper's 3v3 label and its bracket were 0.4 mm from the
    # new edge; half a millimetre in, they still sit left of J11's pin 1.
    text = replace_once(text, '(gr_text "3v3"\n\t\t(at 79.8 52.5 90)',
                        '(gr_text "3v3"\n\t\t(at 80.3 52.5 90)')
    text = replace_once(text, "(gr_line\n\t\t(start 79.3 54.3)\n\t\t(end 79.3 52.9)",
                        "(gr_line\n\t\t(start 79.8 54.3)\n\t\t(end 79.8 52.9)")
    return text, gone


def pad_anchors(text: str) -> list[tuple[float, float, float, set[str]]]:
    """(x, y, reach, layers) of every pad left, in KiCad coordinates.

    Reach is half the pad's larger side: a track ends somewhere on the
    pad's copper, not at its centre.
    """
    out = []
    for a, b in top_level_blocks(text, "footprint"):
        block = text[a:b]
        fx, fy = fp_origin(block)
        m = re.search(r'\n\t\t\(at [-\d.]+ [-\d.]+(?: ([-\d.]+))?\)', block)
        rot = float(m.group(1) or 0)
        for pad in re.finditer(r'\n\t\t\(pad "[^"]*" (\w+) \w+\n', block):
            pb = block[pad.start() + 3:block_end(block, pad.start() + 3)]
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+)', pb)
            size = re.search(r'\(size ([-\d.]+) ([-\d.]+)', pb)
            layers = re.search(r'\(layers ([^)]*)\)', pb).group(1)
            px, py = float(at.group(1)), float(at.group(2))
            # Footprint rotation is counter-clockwise on a Y-down screen.
            import math
            t = math.radians(rot)
            x = fx + px * math.cos(t) + py * math.sin(t)
            y = fy - px * math.sin(t) + py * math.cos(t)
            reach = max(float(size.group(1)), float(size.group(2))) / 2
            ls = {"F.Cu", "B.Cu"} if '"*.Cu"' in layers else \
                {l.strip('"') for l in layers.split() if l.strip('"').endswith(".Cu")}
            out.append((x, y, reach, ls))
    return out


def drop_copper(text: str) -> tuple[str, list[str]]:
    """Tracks and vias in the cut, then whatever that leaves dangling.

    A segment end is connected if a pad's copper, a via, or another
    segment's end on its layer is there.  Anything with a free end is
    removed and the test is run again, until the copper that is left is
    all copper that goes somewhere.  Ground stubs into the pour would
    survive KiCad's own check; here they go too, which loses nothing.
    """
    gone = []
    pads = pad_anchors(text)

    def parse():
        segs, vias = [], []
        for a, b in top_level_blocks(text, "segment"):
            blk = text[a:b]
            s = re.search(r'\(start ([-\d.]+) ([-\d.]+)\)', blk)
            e = re.search(r'\(end ([-\d.]+) ([-\d.]+)\)', blk)
            layer = re.search(r'\(layer "([^"]+)"\)', blk).group(1)
            segs.append((a, b, (float(s[1]), float(s[2])),
                         (float(e[1]), float(e[2])), layer))
        for a, b in top_level_blocks(text, "via"):
            blk = text[a:b]
            at = re.search(r'\(at ([-\d.]+) ([-\d.]+)\)', blk)
            vias.append((a, b, (float(at[1]), float(at[2]))))
        return segs, vias

    def near(p, q, tol=0.02):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    # First pass: anything in the cut.
    segs, vias = parse()
    spans = []
    for a, b, s, e, layer in segs:
        if to_board(*s)[0] < CUT_X or to_board(*e)[0] < CUT_X:
            spans.append((a, b))
            gone.append(f"segment {to_board(*s)} - {to_board(*e)} {layer}")
    for a, b, at in vias:
        if to_board(*at)[0] < CUT_X:
            spans.append((a, b))
            gone.append(f"via {to_board(*at)}")
    text = remove_blocks(text, spans)

    # Then, until nothing changes: free ends.
    while True:
        segs, vias = parse()
        ends = {}
        for _, _, s, e, layer in segs:
            for p in (s, e):
                ends.setdefault(layer, []).append(p)
        via_pts = [at for _, _, at in vias]

        def connected(p, layer):
            if any(near(p, v) for v in via_pts):
                return True
            for x, y, reach, ls in pads:
                if layer in ls and abs(p[0] - x) <= reach + 0.02 \
                        and abs(p[1] - y) <= reach + 0.02:
                    return True
            return sum(1 for q in ends.get(layer, []) if near(p, q)) >= 2

        spans = []
        for a, b, s, e, layer in segs:
            if not (connected(s, layer) and connected(e, layer)):
                spans.append((a, b))
                gone.append(f"dangling segment {to_board(*s)} - "
                            f"{to_board(*e)} {layer}")
        for a, b, at in vias:
            touching = sum(1 for layer in ends for q in ends[layer]
                           if near(at, q))
            # A via with one track into it and nothing else is a dead end.
            under_pad = any(abs(at[0] - x) <= reach and abs(at[1] - y) <= reach
                            for x, y, reach, _ in pads)
            if touching <= 1 and not under_pad:
                spans.append((a, b))
                gone.append(f"dangling via {to_board(*at)}")
        if not spans:
            return text, gone
        text = remove_blocks(text, spans)


def strip_zone_fills(text: str) -> str:
    """The pour is refilled by KiCad; a stale fill would cover the cut."""
    spans = []
    for a, b in top_level_blocks(text, "zone"):
        block = text[a:b]
        i = 0
        while True:
            j = block.find("\n\t\t(filled_polygon", i)
            if j < 0:
                break
            k = block_end(block, j + 3)
            spans.append((a + j, a + k))
            i = k
    return remove_blocks(text, spans)


# ---------------------------------------------------------------------------
# the two new footprints
# ---------------------------------------------------------------------------

def fp_line(start, end, layer, width=0.12) -> str:
    return (f"\n\t\t(fp_line\n\t\t\t(start {start[0]:g} {start[1]:g})"
            f"\n\t\t\t(end {end[0]:g} {end[1]:g})\n\t\t\t(stroke\n\t\t\t\t"
            f"(width {width:g})\n\t\t\t\t(type solid)\n\t\t\t)\n\t\t\t"
            f"(layer \"{layer}\")\n\t\t\t(uuid \"{new_uuid()}\")\n\t\t)")


def fp_rect(start, end, layer, width=0.12) -> str:
    return (f"\n\t\t(fp_rect\n\t\t\t(start {start[0]:g} {start[1]:g})"
            f"\n\t\t\t(end {end[0]:g} {end[1]:g})\n\t\t\t(stroke\n\t\t\t\t"
            f"(width {width:g})\n\t\t\t\t(type default)\n\t\t\t)\n\t\t\t"
            f"(fill no)\n\t\t\t(layer \"{layer}\")\n\t\t\t"
            f"(uuid \"{new_uuid()}\")\n\t\t)")


def rect_lines(x0, y0, x1, y1, layer, width) -> str:
    return (fp_line((x0, y0), (x1, y0), layer, width)
            + fp_line((x1, y0), (x1, y1), layer, width)
            + fp_line((x1, y1), (x0, y1), layer, width)
            + fp_line((x0, y1), (x0, y0), layer, width))


def set_property(block: str, name: str, value: str) -> str:
    pat = re.compile(rf'\(property "{name}" "(?:[^"\\]|\\.)*"')
    if not pat.search(block):
        raise SystemExit(f"no property {name!r} in the footprint")
    return pat.sub(f'(property "{name}" "{value}"', block, count=1)


def strip_graphics(block: str) -> str:
    """Every fp_line, fp_rect, fp_text and model, leaving pads and fields."""
    spans = []
    for head in ("fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly",
                 "fp_text", "model"):
        i = 0
        key = f"\n\t\t({head}"
        while True:
            j = block.find(key, i)
            if j < 0:
                break
            # Whole tokens only: "(fp_line" must not match "(fp_line_x".
            if block[j + len(key)] not in " \n\t":
                i = j + 1
                continue
            k = block_end(block, j + 3)
            spans.append((j, k))
            i = k
    if not spans:
        raise SystemExit("strip_graphics found nothing to strip")
    return remove_blocks(block, spans)


def insert_before_pads(block: str, graphics: str) -> str:
    i = block.find("\n\t\t(pad ")
    return block[:i] + graphics + block[i:]


def append_before_close(block: str, tail: str) -> str:
    assert block.endswith("\n\t)")
    return block[:-3] + tail + "\n\t)"


def socket_footprint(block: str) -> str:
    """J1: keep the forty plated holes, add the socket's SMT pads under them."""
    block = block.replace(f'(footprint "{OLD_SOCKET}"', f'(footprint "{SOCKET}"', 1)
    block = re.sub(r'\(descr "[^"]*"\)',
                   '(descr "2x20 low-profile SMT socket on the underside, '
                   'Kaweei CS25582-40G-M36-0A (Adafruit 2187), 3.8 mm tall; '
                   'the pins pass through the plated holes")', block, count=1)
    block = re.sub(r'\(tags "[^"]*"\)',
                   '(tags "socket SMD 2x20 2.54mm Raspberry Pi GPIO low profile")',
                   block, count=1)
    block = set_property(block, "Value", "CS25582-40G-M36-0A")
    block = set_property(block, "Datasheet", "https://www.adafruit.com/product/2187")
    block = set_property(block, "Description",
                         "Raspberry Pi GPIO socket, SMT, 3.8 mm, on the underside")
    block = block.replace("(attr through_hole)", "(attr smd)", 1)
    # Each SMT pad carries its hole's number.  KiCad treats two pads with
    # one number as joined only when the footprint says so.
    block = replace_once(block, "(duplicate_pad_numbers_are_jumpers no)",
                         "(duplicate_pad_numbers_are_jumpers yes)")
    block = strip_graphics(block)

    # The SMT pads, one beside each hole, same number and net.
    smt = ""
    for m in list(re.finditer(r'\n\t\t\(pad "(\d+)" thru_hole', block)):
        n = int(m.group(1))
        pb = block[m.start() + 3:block_end(block, m.start() + 3)]
        at = re.search(r'\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)', pb)
        px, py, rot = float(at[1]), float(at[2]), at[3] or "0"
        keep = "".join(f"\n\t\t\t{line.strip()}" for line in pb.splitlines()
                       if line.strip().startswith(("(net ", "(pinfunction ",
                                                   "(pintype ")))
        row = 1.27 - SMT_ROW if px == 0 else 1.27 + SMT_ROW
        smt += (f'\n\t\t(pad "{n}" smd rect\n\t\t\t(at {row:g} {py:g} {rot})'
                f"\n\t\t\t(size {SMT_PAD[0]:g} {SMT_PAD[1]:g})"
                f'\n\t\t\t(layers "B.Cu" "B.Mask" "B.Paste")'
                f"{keep}\n\t\t\t(uuid \"{new_uuid()}\")\n\t\t)")

    # Body 7.6 wide about the centre line, the pitch long plus an end each
    # side; courtyard outside the pads.  On the back, where the part is; a
    # fab outline on the front too, so the front says what the holes are
    # for.  Silk only at the two ends and at pin 1: the long sides would
    # run over the pads, and the outer one over the board's edge.
    cx = 1.27
    y0, y1 = -1.27 - 0.5, 48.26 + 1.27 + 0.5
    body = (cx - 3.8, y0, cx + 3.8, y1)
    reach = SMT_ROW + SMT_PAD[0] / 2
    court = (cx - reach - 0.25, y0 - 0.65, cx + reach + 0.25, y1 + 0.65)
    g = rect_lines(*body, "B.Fab", 0.1) + rect_lines(*body, "F.Fab", 0.1)
    g += rect_lines(*court, "B.CrtYd", 0.05)
    for y in (y0 - 0.4, y1 + 0.4):
        g += fp_line((cx - 3.5, y), (cx + 3.5, y), "B.SilkS", 0.12)
    # Pin 1: a chamfer on the fab outlines and a bar on the silk, at the
    # end where the square pad is.
    g += fp_line((body[0], y0 + 1.5), (body[0] + 1.5, y0), "B.Fab", 0.1)
    g += fp_line((body[0], y0 + 1.5), (body[0] + 1.5, y0), "F.Fab", 0.1)
    g += fp_line((cx - 3.5, y0 - 0.4), (cx - 3.5, y0 + 1.0), "B.SilkS", 0.3)
    g += ('\n\t\t(fp_text user "${REFERENCE}"\n\t\t\t(at 1.27 24.13 90)'
          '\n\t\t\t(layer "F.Fab")\n\t\t\t(uuid "%s")\n\t\t\t(effects'
          '\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)'
          '\n\t\t\t\t)\n\t\t\t)\n\t\t)' % new_uuid())
    block = insert_before_pads(block, g)
    block = append_before_close(block, smt)
    return block


#: The Tiny Tapeout library's right-angle Pmod peripheral footprint,
#: PinHeader_2x06_P2.54mm_PMODPeriph2B, as line segments in its own frame:
#: pin rows at x = 0 and 2.54, the body from x = 4.04 to 6.58, the pins out
#: to 12.58.  Mirrored in y (y -> 12.7 - y) it numbers the way the underside
#: plugs it replaces do, and that is the frame the graphics below are in.
_TT_RA_SILK = [
    ((-1.27, -1.27), (0, -1.27)), ((-1.27, 0), (-1.27, -1.27)),
    ((3.98, -1.33), (3.98, 14.03)), ((6.64, 14.03), (6.64, -1.33)),
    ((6.64, -1.33), (3.98, -1.33)),
] + [((3.98, y), (6.64, y)) for y in (1.27, 3.81, 6.35, 8.89, 11.43, 14.03)] \
  + [((6.64, y), (12.64, y)) for y in (-0.38, 0.38, 2.16, 2.92, 4.7, 5.46,
                                         7.24, 8, 9.78, 10.54, 12.32, 13.08)] \
  + [((12.64, y), (12.64, y + 0.76)) for y in (-0.38, 2.16, 4.7, 7.24, 9.78, 12.32)] \
  + [((1.042929, y), (1.497071, y)) for y in (2.16, 2.92, 4.7, 5.46, 7.24, 8,
                                                9.78, 10.54, 12.32, 13.08)] \
  + [((1.11, -0.38), (1.497071, -0.38)), ((1.11, 0.38), (1.497071, 0.38))] \
  + [((3.582929, y), (3.98, y)) for y in (-0.38, 0.38, 2.16, 2.92, 4.7, 5.46,
                                            7.24, 8, 9.78, 10.54, 12.32, 13.08)]
#: The thick GND marker round pin 5, and its bars.
_TT_RA_SILK_HEAVY = [((-1.2, 1.55), (3.85, 1.55), 0.3), ((-1.2, 3.65), (3.85, 3.65), 0.3),
                     ((-1.1, 1.6), (-1.1, 3.6), 0.4), ((3.6, 1.6), (3.6, 3.6), 0.4),
                     ((3.75, 1.6), (3.75, 3.6), 0.4), ((1.25, 1.8), (1.25, 3.45), 0.8)]
_TT_RA_FAB = [
    ((-0.32, -0.32), (-0.32, 0.32)), ((-0.32, -0.32), (4.04, -0.32)),
    ((-0.32, 0.32), (4.04, 0.32)),
    ((4.04, -0.635), (4.675, -1.27)), ((4.04, 13.97), (4.04, -0.635)),
    ((4.675, -1.27), (6.58, -1.27)), ((6.58, -1.27), (6.58, 13.97)),
    ((6.58, 13.97), (4.04, 13.97)),
] + [seg for y in (2.22, 4.76, 7.3, 9.84, 12.38) for seg in
     (((-0.32, y), (-0.32, y + 0.64)), ((-0.32, y), (4.04, y)),
      ((-0.32, y + 0.64), (4.04, y + 0.64)))] \
  + [seg for y in (-0.32, 2.22, 4.76, 7.3, 9.84, 12.38) for seg in
     (((6.58, y), (12.58, y)), ((6.58, y + 0.64), (12.58, y + 0.64)),
      ((12.58, y), (12.58, y + 0.64)))]
#: The library's courtyard reaches 1.8 behind the far pad row; here that
#: touched the courtyards of the three capacitors behind the plugs by a few
#: hundredths, so it stops at 1.45, still 0.6 clear of the pads.
_TT_RA_COURT = (-1.45, -1.8, 13.1, 14.5)
#: The board edge crosses the plug's body: silk past this is off the board.
_TT_RA_SILK_LIMIT = 5.2


def _mirror(p):
    return (p[0], round(12.7 - p[1], 6))


def _on_board(a, b) -> bool:
    return a[0] <= _TT_RA_SILK_LIMIT and b[0] <= _TT_RA_SILK_LIMIT


def plug_footprint(block: str) -> str:
    """J2-J4: the same twelve pads, a right-angle header drawn over them."""
    block = block.replace(f'(footprint "{OLD_PLUG}"', f'(footprint "{PLUG}"', 1)
    block = re.sub(r'\(descr "[^"]*"\)',
                   '(descr "Right-angle pin header, 2x06, 2.54mm pitch, 6 mm '
                   'pins out past the board edge, on the component side; '
                   'the Tiny Tapeout PMODPeriph2B right-angle footprint '
                   'mirrored to number like the underside plug it replaces")',
                   block, count=1)
    block = re.sub(r'\(tags "[^"]*"\)',
                   '(tags "PMOD peripheral, digilent numbering, right angle pin header THT 2x06 2.54mm double row")',
                   block, count=1)
    block = set_property(block, "Value", "61301221021")
    block = set_property(block, "Datasheet",
                         "https://www.we-online.com/components/products/datasheet/61301221021.pdf")
    block = set_property(block, "Description",
                         "Male PMOD peripheral connector, right angle, on top")
    # The reference was on the back, mirrored; put it on the front, behind
    # the pads and clear of the silk box round the plug row, upright.
    block = re.sub(r'(\(property "Reference" "[^"]*"\n\t\t\t\(at )[-\d. ]+\)'
                   r'\n\t\t\t\(layer "B.SilkS"\)',
                   r'\g<1>-3.4 6.35 0)\n\t\t\t(layer "F.SilkS")', block, count=1)
    block = block.replace("\n\t\t\t\t(justify mirror)", "", 1)
    block = strip_graphics(block)
    g = ""
    for a, b in _TT_RA_SILK:
        if _on_board(a, b):
            g += fp_line(_mirror(a), _mirror(b), "F.SilkS", 0.12)
    # The library's heavy bars round the GND pins come within 0.15 mm of
    # the pads; the box round them says the same thing and stays.
    g += fp_rect(_mirror((-1.3, 1.45)), _mirror((3.98, 3.81)), "F.SilkS", 0.12)
    for a, b in _TT_RA_FAB:
        g += fp_line(_mirror(a), _mirror(b), "F.Fab", 0.1)
    x0, y0, x1, y1 = _TT_RA_COURT
    g += rect_lines(x0, 12.7 - y1, x1, 12.7 - y0, "F.CrtYd", 0.05)
    g += ('\n\t\t(fp_text user "${REFERENCE}"\n\t\t\t(at 5.31 6.35 90)'
          '\n\t\t\t(layer "F.Fab")\n\t\t\t(uuid "%s")\n\t\t\t(effects'
          '\n\t\t\t\t(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)'
          '\n\t\t\t\t)\n\t\t\t)\n\t\t)' % new_uuid())
    block = insert_before_pads(block, g)
    model = ('\n\t\t(model "${KICAD10_3DMODEL_DIR}/Connector_PinHeader_2.54mm'
             '.3dshapes/PinHeader_2x06_P2.54mm_Horizontal.step"\n\t\t\t(offset'
             '\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n\t\t\t(scale\n\t\t\t\t(xyz 1 1 1)'
             '\n\t\t\t)\n\t\t\t(rotate\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n\t\t)')
    return append_before_close(block, model)


def swap_footprints(text: str) -> str:
    done = {"J1": 0, "J2": 0, "J3": 0, "J4": 0}
    for a, b in reversed(top_level_blocks(text, "footprint")):
        block = text[a:b]
        ref = reference_of(block)
        if ref == "J1":
            assert OLD_SOCKET in block
            text = text[:a] + block[:1] + socket_footprint(block[1:]) + text[b:]
        elif ref in ("J2", "J3", "J4"):
            assert OLD_PLUG in block
            text = text[:a] + block[:1] + plug_footprint(block[1:]) + text[b:]
        else:
            continue
        done[ref] += 1
    if any(v != 1 for v in done.values()):
        raise SystemExit(f"footprints swapped: {done}")
    return text


def library_copy(block: str, name: str) -> str:
    """A .kicad_mod of the footprint: the instance with its placement gone.

    A placed footprint stores its pads' and its texts' angles with the
    footprint's own rotation added in; a library file stores them as the
    footprint has them, so the rotation comes back out.
    """
    m = block
    m = re.sub(r'\(footprint "RaspmodDirect:([^"]+)"',
               r'(footprint "\1"\n\t(version 20260206)\n\t(generator "pcbnew")'
               r'\n\t(generator_version "10.0")', m, count=1)
    placed = re.search(r'\n\t\t\(uuid "[^"]*"\)\n\t\t\(at [-\d.]+ [-\d.]+(?: ([-\d.]+))?\)', m)
    rot = float(placed.group(1) or 0)
    m = m[:placed.start()] + m[placed.end():]

    def unrotate(match):
        angle = (float(match.group(3)) - rot) % 360
        return f"(at {match.group(1)} {match.group(2)} {angle:g})"
    # Pads and property texts carry a third value; graphics do not.
    m = re.sub(r"\(at ([-\d.]+) ([-\d.]+) ([-\d.]+)\)", unrotate, m)
    for head in ("path", "sheetname", "sheetfile"):
        m = re.sub(rf'\n\t\t\({head} "[^"]*"\)', "", m)
    m = re.sub(r'\n\t+\(uuid "[^"]*"\)', "", m)
    m = re.sub(r'\n\t+\(net "[^"]*"\)', "", m)
    m = re.sub(r'\n\t+\(pinfunction "[^"]*"\)', "", m)
    m = re.sub(r'\n\t+\(pintype "[^"]*"\)', "", m)
    m = set_property(m, "Reference", "REF**")
    # Instance indentation is one tab deeper than a library file's.
    m = "\n".join(line[1:] if line.startswith("\t") else line
                  for line in m.splitlines())
    return m + "\n"


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

def reword(text: str) -> str:
    edits = [
        ('(gr_text "v1.1"', '(gr_text "v2.0"'),
        ('(gr_text "Pi Ribbon"', '(gr_text "Pi header below"'),
        ('(title "TT Demoboard To Raspi")', '(title "TT Demoboard To Raspi Direct")'),
        ('(date "2024-12-14")', '(date "2026-09-29")'),
        ('(rev "1.0")', '(rev "2.0")'),
        ('(comment 1 "(C) 2024 Pat Deegan")',
         '(comment 1 "(C) 2024 Pat Deegan")\n\t\t(comment 2 "Direct-on-GPIO '
         'variant, 2026, Tim Ansell: J1 an SMT socket underneath, J2-J4 right '
         'angle, 10 mm shorter")'),
    ]
    for old, new in edits:
        text = replace_once(text, old, new)
    # The back carried two texts: a one-liner about the ribbon, which goes,
    # and a three-line explanation, which is rewritten.  Both were anchored
    # at their right-hand end and ran left, so the new one is kept to the
    # old one's length, and it moves down into the band between the host
    # sockets' pads and the socket's own SMT pads, the only clear strip
    # left on that side.  The line breaks are backslash-n in the file.
    for a, b in reversed(top_level_blocks(text, "gr_text")):
        if text[a:b].startswith('\n\t(gr_text "Connect 3 PMODs to RPi I/O'):
            text = text[:a] + text[b:]
            break
    else:
        raise SystemExit("the back's ribbon text is not where expected")
    pat = re.compile(r'\(gr_text "With a Tiny Tapeout demoboard, (?:[^"\\]|\\.)*"'
                     r'\n\t\t\(at 130\.9 55\.4 0\)')
    if len(pat.findall(text)) != 1:
        raise SystemExit("the back's explanation text is not where expected")
    # Anchored at its bottom edge, so it stands on y = 16.9 and its three
    # lines reach up to about 20.2: over the host sockets' pads, under the
    # socket's own.
    new = ('(gr_text "Direct variant: this side\'s socket sits on the Pi\'s '
           "\\nGPIO header; the front's right-angle plugs go into a "
           "\\ndemoboard's or an Arty's Pmod hosts.\"\n\t\t(at 130.9 56.2 0)")
    text = pat.sub(lambda m: new, text)
    return text


# ---------------------------------------------------------------------------
# the schematic
# ---------------------------------------------------------------------------

def edit_schematic(text: str) -> str:
    n = text.count(OLD_SOCKET)
    text = text.replace(OLD_SOCKET, SOCKET)
    print(f"schematic: {n} footprint field(s) -> socket")
    n = text.count(OLD_PLUG)
    text = text.replace(OLD_PLUG, PLUG)
    print(f"schematic: {n} footprint field(s) -> right-angle plug")
    # The parts that are gone stay on the schematic, marked not fitted and
    # not on the board, so their wiring still reads and ERC still passes.
    seen = set()
    key = "\n\t(symbol\n\t\t(lib_id"
    i = 0
    while True:
        i = text.find(key, i)
        if i < 0:
            break
        a = i + 2
        b = block_end(text, a)
        block = text[a:b]
        ref = reference_of(block)
        if ref in UNFITTED_REFS:
            for old, new in (("(in_bom yes)", "(in_bom no)"),
                             ("(on_board yes)", "(on_board no)"),
                             ("(dnp no)", "(dnp yes)")):
                if block.count(old) > 1:
                    raise SystemExit(f"{ref}: more than one {old}")
                block = block.replace(old, new)
            text = text[:a] + block + text[b:]
            seen.add(ref)
        elif ref == "J1":
            # The symbol's value, datasheet and description name the part
            # now fitted, as the footprint's do, so the parity check is
            # content.
            block = set_property(block, "Value", "CS25582-40G-M36-0A")
            block = set_property(block, "Datasheet", "https://www.adafruit.com/product/2187")
            block = set_property(block, "Description",
                                 "Raspberry Pi GPIO socket, SMT, 3.8 mm, on the underside")
            text = text[:a] + block + text[b:]
        elif ref in ("J2", "J3", "J4"):
            block = set_property(block, "Value", "61301221021")
            block = set_property(block, "Datasheet",
                                 "https://www.we-online.com/components/products/datasheet/61301221021.pdf")
            block = set_property(block, "Description",
                                 "Male PMOD peripheral connector, right angle, on top")
            text = text[:a] + block + text[b:]
        i = a + len(block)
    if seen != UNFITTED_REFS:
        raise SystemExit(f"schematic: marked {sorted(seen)}, wanted "
                         f"{sorted(UNFITTED_REFS)}")
    for old, new in [
        ('(title "Tiny Tapeout Demoboard to Raspberry Pi/Ribbon Con")',
         '(title "Tiny Tapeout Demoboard to Raspberry Pi, direct on the GPIO header")'),
        ('(rev "1.0")\n\t\t(company', '(date "2026-09-29")\n\t\t(rev "2.0")\n\t\t(company'),
    ]:
        text = replace_once(text, old, new)
    return disconnect_pin17(text)


#: The Raspberry Pi symbol draws its two 3V3 pins on one point, so that one
#: wire feeds both.  Pin 17 is left off the rail on the board (see RAIL_Y),
#: so on the schematic it is unstacked to the free spot under them and
#: marked no-connect.  Sheet position: the symbol's origin plus the pin's,
#: with the library's Y up and the sheet's down.
SYMBOL_AT = (123.19, 71.12)
PIN17_FROM = "(at 33.02 20.32 180)"
PIN17_TO = "(at 33.02 17.78 180)"
PIN17_SHEET = (SYMBOL_AT[0] + 33.02, SYMBOL_AT[1] - 17.78)


def disconnect_pin17(text: str) -> str:
    i = text.index('(symbol "Connector:Raspberry_Pi_4"')
    j = block_end(text, i)
    lib = text[i:j]
    if f'(at {SYMBOL_AT[0]:g} {SYMBOL_AT[1]:g} 0)' not in text:
        raise SystemExit("J1 is not where the pin-17 arithmetic expects")
    # The two stacked pins are consecutive blocks; find pin 17's own.
    blocks = [(m.start(), block_end(lib, m.start()))
              for m in re.finditer(r"\(pin \w+ line\s*" + re.escape(PIN17_FROM), lib)]
    hits = [(a, b) for a, b in blocks if '(number "17"' in lib[a:b]]
    if len(hits) != 1:
        raise SystemExit("pin 17 of the Raspberry Pi symbol is not stacked where expected")
    a, b = hits[0]
    pin = lib[a:b]
    pin = re.sub(r"^\(pin \w+ line", "(pin no_connect line", pin, count=1)
    pin = pin.replace(PIN17_FROM, PIN17_TO, 1)
    lib = lib[:a] + pin + lib[b:]
    text = text[:i] + lib + text[j:]
    x, y = PIN17_SHEET
    for w in re.finditer(r"\(xy ([-\d.]+) ([-\d.]+)\)", text):
        if abs(float(w[1]) - x) < 0.05 and abs(float(w[2]) - y) < 0.05:
            raise SystemExit(f"something already touches ({x}, {y}) on the sheet")
    marker = (f'\n\t(no_connect\n\t\t(at {x:g} {y:g})\n\t\t(uuid "{new_uuid()}")'
              "\n\t)")
    k = text.index("\n\t(symbol\n\t\t(lib_id")
    return text[:k] + marker + text[k:]


# ---------------------------------------------------------------------------

def main() -> None:
    pcb = PCB.read_text()
    if SOCKET in pcb:
        raise SystemExit("already the direct variant")
    pcb = cut_outline(pcb)
    pcb, parts = drop_footprints(pcb)
    print("parts removed:\n  " + "\n  ".join(parts))
    pcb, silk = drop_graphics(pcb)
    print("graphics removed:\n  " + "\n  ".join(silk))
    pcb, copper = drop_copper(pcb)
    print(f"copper removed: {len(copper)} items\n  " + "\n  ".join(copper))
    pcb = rebuild_vdd_rail(pcb)
    print(f"VDD rail: redrawn on the front at y = {RAIL_Y}")
    pcb = ground_stubs(pcb)
    pcb = header_links(pcb)
    pcb = strip_zone_fills(pcb)
    pcb = swap_footprints(pcb)
    pcb = release_pin17(pcb)
    pcb = reword(pcb)
    PCB.write_text(pcb)

    LIB.mkdir(exist_ok=True)
    for a, b in top_level_blocks(pcb, "footprint"):
        block = pcb[a + 2:b]
        ref = reference_of(block)
        if ref == "J1":
            (LIB / (SOCKET.split(":")[1] + ".kicad_mod")).write_text(
                library_copy(block, SOCKET))
        elif ref == "J2":
            (LIB / (PLUG.split(":")[1] + ".kicad_mod")).write_text(
                library_copy(block, PLUG))

    SCH.write_text(edit_schematic(SCH.read_text()))
    print("done")


if __name__ == "__main__":
    main()
