#!/bin/sh
# Run KiCad 10's command line against this project, in KiCad's own Docker
# image, because the board and schematic are in KiCad 10's file format and
# a distribution's KiCad 9 refuses to load them.
#
#   scripts/kicad10.sh check          DRC (with schematic parity) and ERC,
#                                     reports into tmp/
#   scripts/kicad10.sh images         3D renders and the copper plot, into
#                                     images/
#   scripts/kicad10.sh refill         refill the ground pour and save, via
#                                     KiCad's Python (scripts/refill.py)
#   scripts/kicad10.sh verify         scripts/check_direct.py, pad for pad
#                                     against upstream's board
#   scripts/kicad10.sh py <script>    a script under KiCad's Python
#   scripts/kicad10.sh -- <args...>   any kicad-cli invocation
#
# Everything runs as the calling user so the files it writes are yours.
set -eu
cd "$(dirname "$0")/.."
#: The -full image carries KiCad's 3D models, so the renders show the
#: connectors and not just their pads.
IMAGE=kicad/kicad:10.0-full
run() {
    docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work \
        -w /work "$IMAGE" "$@"
}
mkdir -p tmp images
case "${1:-}" in
  check)
    run kicad-cli pcb drc --severity-all --schematic-parity \
        -o tmp/drc.txt TTDB_2_Raspi.kicad_pcb
    run kicad-cli sch erc --severity-all -o tmp/erc.txt TTDB_2_Raspi.kicad_sch
    ;;
  images)
    for side in top bottom; do
      run kicad-cli pcb render --side "$side" -w 1600 -h 900 --quality high \
          --floor --background opaque -o "images/direct-$side.png" \
          TTDB_2_Raspi.kicad_pcb
    done
    run kicad-cli pcb render --rotate -35,0,30 --perspective -w 1600 -h 900 \
        --quality high --floor --background opaque \
        -o images/direct-iso.png TTDB_2_Raspi.kicad_pcb
    run kicad-cli pcb export svg --layers F.Cu,B.Cu,Edge.Cuts,F.SilkS \
        --page-size-mode 2 --exclude-drawing-sheet --mode-single \
        -o images/direct-copper.svg TTDB_2_Raspi.kicad_pcb
    ;;
  refill)
    run python3 scripts/refill.py TTDB_2_Raspi.kicad_pcb
    ;;
  build)
    # The whole transformation from upstream's files, which are expected
    # to be in place: edit, fill, restitch, fill, then prove and check.
    python3 scripts/make_direct.py
    run python3 scripts/refill.py TTDB_2_Raspi.kicad_pcb
    python3 scripts/restitch.py
    run python3 scripts/refill.py TTDB_2_Raspi.kicad_pcb
    ;;
  verify)
    run python3 scripts/check_direct.py "${2:-tmp/upstream.kicad_pcb}" \
        TTDB_2_Raspi.kicad_pcb
    ;;
  py)
    shift
    run python3 "$@"
    ;;
  --)
    shift
    run kicad-cli "$@"
    ;;
  *)
    sed -n 2,16p "$0"
    exit 2
    ;;
esac
