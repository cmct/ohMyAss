#!/bin/sh
# Build the x64 avs2bdnxml used by OMA.
#
# Requirements:
#   w64devkit (gcc, ar)
#   zlib 1.3.1 source
#   libpng 1.6.44 source
#   AviSynth+ x64 headers (avisynth_c.h, avisynth_c_loader.h)
#   avs2bdnxml-master source + avs2bdnxml-x64.patch
#
# Usage:
#   ./build-avs2bdnxml-x64.sh /path/to/avs2bdnxml-src \
#       /path/to/avisynth-include /path/to/zlib /path/to/libpng
#
set -e

SRC="$1"
AVS_INC="$2"
ZLIB="$3"
LIBPNG="$4"

if [ -z "$SRC" ] || [ -z "$AVS_INC" ] || [ -z "$ZLIB" ] || [ -z "$LIBPNG" ]; then
    echo "usage: $0 <avs2bdnxml-src> <avisynth-include> <zlib> <libpng>" >&2
    exit 2
fi

cd "$SRC"
CFLAGS="-O2 -Wall -DLE_ARCH -I. -I$AVS_INC -I$ZLIB -I$LIBPNG"

for f in avs2bdnxml.c auto_split.c palletize.c sup.c sort.c ass.c; do
    gcc $CFLAGS -c "$f" -o "${f%.c}.o"
done

gcc -o avs2bdnxml.exe \
    avs2bdnxml.o auto_split.o palletize.o sup.o sort.o ass.o \
    "$LIBPNG/libpng.a" "$ZLIB/libz.a" -lm

strip avs2bdnxml.exe
