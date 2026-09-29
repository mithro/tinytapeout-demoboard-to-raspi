"""Refill every zone on a board and save it, under KiCad's own Python.

make_direct.py strips the ground pour's stale fill, since the fill it found
covered the part of the board that was cut off.  KiCad refills a pour when
the board is opened and the user says yes; this does the same without the
question, so the committed board carries a fill that matches its outline.

Run: scripts/kicad10.sh refill
"""
import sys

import pcbnew

board = pcbnew.LoadBoard(sys.argv[1])
zones = board.Zones()
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(zones)
pcbnew.SaveBoard(sys.argv[1], board)
print(f"refilled {len(zones)} zone(s) and saved {sys.argv[1]}")
