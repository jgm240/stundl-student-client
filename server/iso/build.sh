#!/bin/bash
# Builds the SSC Server ISO.  Usage: server/iso/build.sh amd64|i386 [version]
# Run as root on Debian/Ubuntu with live-build installed (CI uses a debian:bookworm container).
set -euo pipefail
ARCH=${1:?amd64 or i386}
VERSION=${2:-dev}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
WORK=${WORK:-/tmp/ssc-iso-$ARCH}
rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"

case "$ARCH" in
  amd64) FLAVOUR=amd64;    BOOT="--bootloaders syslinux,grub-efi" ;;
  i386)  FLAVOUR=686-pae;  BOOT="--bootloaders syslinux" ;;
  *) echo "unknown arch $ARCH"; exit 2 ;;
esac

lb config noauto \
  --distribution bookworm --architectures "$ARCH" --linux-flavours "$FLAVOUR" \
  --binary-images iso-hybrid $BOOT --debian-installer none \
  --archive-areas "main non-free-firmware" --apt-recommends false --apt-indices false \
  --iso-volume "SSC-SERVER-$ARCH" --iso-application "SSC Server" --iso-publisher "SSC Server project" \
  --bootappend-live "boot=live components splash=0 console=tty0 console=ttyS0,115200" \
  --mirror-bootstrap http://deb.debian.org/debian --mirror-binary http://deb.debian.org/debian \
  --security false --updates true --memtest none

cp -r "$HERE/package-lists/." config/package-lists/
case "$ARCH" in
  amd64) printf 'grub-pc-bin\ngrub-efi-amd64-bin\ngrub-common\ngrub2-common\nefibootmgr\nlinux-image-amd64\n' > config/package-lists/boot.list.chroot ;;
  i386)  printf 'grub-pc-bin\ngrub-common\ngrub2-common\nlinux-image-686-pae\n' > config/package-lists/boot.list.chroot ;;
esac
printf 'live-boot\nlive-config\nlive-config-systemd\n' > config/package-lists/live.list.chroot

mkdir -p config/includes.chroot config/hooks/normal
cp -r "$HERE/overlay/." config/includes.chroot/
install -d config/includes.chroot/opt/ssc/server
cd "$REPO"
cp core.py ssc.py vault.py jobs.py untis_direct.py ssc_imgen.py ssc_version.py updater.py ui.js index.html parent.html LICENSE "$WORK/config/includes.chroot/opt/ssc/"
mkdir -p "$WORK/config/includes.chroot/opt/ssc/assets"; cp assets/icon.png "$WORK/config/includes.chroot/opt/ssc/assets/"
for f in menu_model.py tui.py; do [ -f "$f" ] && cp "$f" "$WORK/config/includes.chroot/opt/ssc/" || true; done
cp server/*.py server/*.html "$WORK/config/includes.chroot/opt/ssc/server/"
echo "$VERSION" > "$WORK/config/includes.chroot/opt/ssc/VERSION"
cd "$WORK"
cp "$HERE/hooks/"* config/hooks/normal/

mkdir -p config/includes.binary
python3 "$REPO/server/sscigen.py" --example > config/includes.binary/autoinstall.ssci.example      # on the stick: a commented example to copy
lb build 2>&1 | tail -n 200
# the commands must be on every user's PATH (Debian's normal users have /usr/local/bin but not the sbin folders)
for c in ssconfig ssc-install ssc-imgen sscigen; do [ -x "chroot/usr/local/bin/$c" ] || { echo "MISSING command in the image: /usr/local/bin/$c"; exit 1; }; done
ls -l binary/live/initrd.img* 2>/dev/null || true
ISO=$(ls ./*.iso | head -1)
OUT="${OUT:-$REPO/dist}"; mkdir -p "$OUT"
cp "$ISO" "$OUT/ssc-server-$VERSION-$ARCH.iso"
( cd "$OUT" && sha256sum "ssc-server-$VERSION-$ARCH.iso" > "ssc-server-$VERSION-$ARCH.iso.sha256" )
ls -lh "$OUT"/ssc-server-*
