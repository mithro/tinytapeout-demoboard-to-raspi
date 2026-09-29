"""Prove the direct variant kept every pad it kept, under KiCad's own Python.

Compares the upstream board with the direct one, footprint by footprint:
every part that is still there has the same pads at the same place, the
same numbers and the same nets; the parts that were meant to go are gone;
J1 gained its forty SMT pads on the back, one per hole, each on its hole's
net; and the outline is 10 mm shorter on the left and nothing else.

Run: scripts/kicad10.sh verify
"""
import sys

import pcbnew

MM = 1_000_000          # pcbnew works in nanometres
REMOVED = {"J9", "J10", "SW1", "R1", "R2", "C1"}
#: How far the socket's SMT pads sit from their holes, across the rows:
#: make_direct.py's SMT_ROW less the half pitch.
SMT_OFFSET = 4.18 - 1.27


def pads(board):
    """(footprint uuid, pad number, kind) -> (x, y, net).

    Keyed by the footprint's uuid, not its reference: the ground stitching
    vias share references (there are several STITCH31), and a reference
    that names two parts cannot say which one moved.
    """
    out = {}
    for fp in board.GetFootprints():
        for p in fp.Pads():
            pos = p.GetPosition()
            key = (fp.m_Uuid.AsString(), p.GetNumber(),
                   "smd" if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD else "tht")
            out[key] = (round(pos.x / MM, 4), round(pos.y / MM, 4), p.GetNetname())
    return out


def refs(board):
    return {fp.m_Uuid.AsString(): fp.GetReference() for fp in board.GetFootprints()}


def outline(board):
    box = board.GetBoardEdgesBoundingBox()
    return (round(box.GetX() / MM, 3), round(box.GetY() / MM, 3),
            round(box.GetRight() / MM, 3), round(box.GetBottom() / MM, 3))


before = pcbnew.LoadBoard(sys.argv[1])
after = pcbnew.LoadBoard(sys.argv[2])
b, a = pads(before), pads(after)
rb, ra = refs(before), refs(after)
problems = []

for ref in REMOVED:
    if ref in ra.values():
        problems.append(f"{ref} is still on the board")

# Every kept part's through-hole pads, unchanged.
kept = sorted(ra[u] for u in ra if u in rb)
for key, val in b.items():
    u, num, kind = key
    if u not in ra:
        continue
    if key not in a:
        problems.append(f"{rb[u]} pad {num} ({kind}) is missing")
    elif rb[u] == "J1" and num == "17":
        # The one deliberate change of net: pin 17 comes off the 3V3 rail,
        # for the reason make_direct.py gives beside RAIL_Y.
        if a[key][:2] != val[:2] or val[2] != "VDD" \
                or a[key][2] != "unconnected-(J1-3V3-Pad17)":
            problems.append(f"J1 pad 17: was {val}, now {a[key]}, expected the "
                            "same place, unconnected")
    elif a[key] != val:
        problems.append(f"{rb[u]} pad {num}: was {val}, now {a[key]}")

# Nothing new but J1's SMT pads.
for key in a:
    if key not in b:
        u, num, kind = key
        if not (ra[u] == "J1" and kind == "smd"):
            problems.append(f"unexpected new pad {ra[u]} {num} ({kind})")
smt = {k[1]: v for k, v in a.items() if ra[k[0]] == "J1" and k[2] == "smd"}
tht = {k[1]: v for k, v in a.items() if ra[k[0]] == "J1" and k[2] == "tht"}
if len(smt) != 40:
    problems.append(f"J1 has {len(smt)} SMT pads, not 40")
for num, (x, y, net) in smt.items():
    hx, hy, hnet = tht[num]
    if net != hnet:
        problems.append(f"J1 SMT pad {num} is on {net!r}, its hole on {hnet!r}")
    # The header's pitch runs along X on this board and its two rows are
    # 2.54 apart in Y, so the SMT pad sits square above or below its hole,
    # outboard of the row, on the socket's own side.
    if abs(x - hx) > 0.001:
        problems.append(f"J1 SMT pad {num} is {x - hx:+.3f} off its hole along the row")
    if abs(abs(y - hy) - SMT_OFFSET) > 0.001:
        problems.append(f"J1 SMT pad {num} at {y - hy:+.3f} from its hole across the rows")
    # Odd pins are the upper row (smaller Y) and their pads go up; even
    # pins the lower row, pads down.
    if (y < hy) != (int(num) % 2 == 1):
        problems.append(f"J1 SMT pad {num} is on the wrong side of its hole")

ob, oa = outline(before), outline(after)
if (round(oa[0] - ob[0], 3), oa[1] - ob[1], oa[2] - ob[2], oa[3] - ob[3]) != (10.0, 0.0, 0.0, 0.0):
    problems.append(f"outline was {ob}, now {oa}: not a 10 mm cut on the left")

for fp in after.GetFootprints():
    for p in fp.Pads():
        if p.GetPosition().x < (oa[0] + 0.5) * MM:
            problems.append(f"{fp.GetReference()} pad {p.GetNumber()} is within 0.5 mm of the new edge")

removed = sorted(rb[u] for u in rb if u not in ra)
print(f"{len(kept)} parts kept, {len(removed)} removed ({', '.join(removed)}), "
      f"{len(b)} pads before, {len(a)} after; outline {ob} -> {oa}")
if problems:
    print("\n".join(problems))
    sys.exit(1)
print("every kept pad is where it was, on its net; J1's 40 SMT pads are "
      "beside their holes on the same nets; the outline is 10 mm shorter on "
      "the left and nothing else")
