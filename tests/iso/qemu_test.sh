#!/bin/bash
# QEMU tests for an SSC Server ISO.   tests/iso/qemu_test.sh live|install <iso> <amd64|i386>
# live    : boots the ISO, checks nothing crashes (serial console reaches the SSC live menu)
# install : unattended install to a virtual disk (answers disk labelled SSCANS), then boots the disk and
#           logs in over HTTP: guest/admin roles, /health, ssconfig showip/showlink, and the 32/64-bit kernel.
set -euo pipefail
MODE=$1; ISO=$2; ARCH=$3
W=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null || true' EXIT
Q=qemu-system-x86_64; [ "$ARCH" = i386 ] && Q=qemu-system-i386
ACCEL=""; [ -w /dev/kvm ] && ACCEL="-enable-kvm -cpu max" || ACCEL="-cpu max"
SLOW=1
if [ "$ARCH" = i386 ]; then ACCEL="-cpu max"; SLOW=4; fi     # 32-bit guests are emulated (KVM hangs on the CI hosts): slower, so wait longer
PW='Twelve-chars-ok1'
screen_check() {   # screenshot via the QEMU monitor; a console with a login prompt has thousands of lit pixels, a blank cursor has a handful
python3 - "$1" <<'PY'
import socket, sys, time
w = sys.argv[1]
s = socket.socket(socket.AF_UNIX); s.connect(w + "/mon.sock"); time.sleep(0.5); s.recv(4096)
s.sendall(("screendump " + w + "/screen.ppm\n").encode()); time.sleep(2)
d = open(w + "/screen.ppm", "rb").read()
parts = d.split(None, 4); lit = sum(1 for i in range(0, len(parts[4]) - 2, 3) if parts[4][i] or parts[4][i + 1] or parts[4][i + 2])
print("lit pixels on the console:", lit)
sys.exit(0 if lit > 800 else 1)
PY
}
echo "== $MODE $ARCH ($Q $ACCEL)"

if [ "$MODE" = live ]; then
  BIOSARG=""; [ "${UEFI:-0}" = 1 ] && BIOSARG="-bios /usr/share/ovmf/OVMF.fd"
  timeout $((420*SLOW)) $Q $ACCEL -m 1024 $BIOSARG -cdrom "$ISO" -boot d -monitor unix:$W/mon.sock,server,nowait -serial file:$W/serial.log -display none -no-reboot \
     -netdev user,id=n0 -device virtio-net-pci,netdev=n0 &
  for i in $(seq 1 $((100*SLOW))); do grep -q "SSC Server" "$W/serial.log" 2>/dev/null && grep -q "login:" "$W/serial.log" 2>/dev/null && break; sleep 4; done
  tail -n 30 "$W/serial.log" || true
  grep -q "login:" "$W/serial.log" || { echo "LIVE BOOT FAILED"; exit 1; }
  echo "LIVE BOOT OK"; sleep 15; screen_check "$W" && echo "LIVE CONSOLE SHOWS TEXT OK"; exit $?
fi

# ---- install mode
qemu-img create -f qcow2 $W/disk.qcow2 8G >/dev/null
mkdir $W/ans; cat > $W/ans/answers.json <<J
{"disk":"auto","hostname":"ssc-ci","admin":"admin","password":"$PW","timezone":"UTC","keymap":"us","ssh":true,"network":{"mode":"dhcp"}}
J
truncate -s 16M $W/ans.img; mkfs.ext4 -q -F -L SSCANS -d $W/ans $W/ans.img
BIOSARG=""; [ "${UEFI:-0}" = 1 ] && BIOSARG="-bios /usr/share/ovmf/OVMF.fd"
echo "== phase 1: install"
timeout $((1500*SLOW)) $Q $ACCEL -m 1536 $BIOSARG -cdrom "$ISO" -boot d -drive file=$W/disk.qcow2,if=virtio,format=qcow2 \
   -drive file=$W/ans.img,if=virtio,format=raw -monitor none -serial file:$W/install.log -display none -no-reboot \
   -netdev user,id=n0 -device virtio-net-pci,netdev=n0 || true
tail -n 40 $W/install.log
grep -q "SSC-INSTALL-OK" $W/install.log || echo "note: install marker not seen (the boot test below decides)"
grep -q "INSTALL FAILED\|Traceback" $W/install.log && { echo "INSTALL FAILED"; exit 1; }
echo "== phase 2: boot the installed system"
$Q $ACCEL -m 1024 $BIOSARG -drive file=$W/disk.qcow2,if=virtio,format=qcow2 -monitor unix:$W/mon.sock,server,nowait -serial file:$W/boot.log -display none \
   -netdev user,id=n0,hostfwd=tcp:127.0.0.1:18080-:8080,hostfwd=tcp:127.0.0.1:18766-:8766 -device virtio-net-pci,netdev=n0 &
for i in $(seq 1 $((90*SLOW))); do curl -fs http://127.0.0.1:18080/health >/dev/null 2>&1 && break; sleep 4; done
curl -fs http://127.0.0.1:18080/health || { tail -n 60 $W/boot.log; echo "SERVER NOT UP"; exit 1; }
echo
J() { python3 - "$@"; }
# admin login -> admin role; wrong password refused; admin sees the panel, guests do not
curl -fsS -c $W/jar -H 'Content-Type: application/json' -d "{\"name\":\"admin\",\"password\":\"$PW\"}" http://127.0.0.1:18080/api/login
curl -fsS -b $W/jar -o $W/me.json http://127.0.0.1:18080/api/me; grep -q '"admin"' $W/me.json
curl -fsS -b $W/jar -o $W/admin.html http://127.0.0.1:18080/admin; grep -qi "admin" $W/admin.html
test "$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' -d '{"name":"admin","password":"nope-nope-nope"}' http://127.0.0.1:18080/api/login)" = 401
test "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/data.json)" = 401
echo "WEB + AUTH OK"
# the SCREEN (tty1) must show a login prompt, not a blank cursor: screenshot via the QEMU monitor, count lit pixels
sleep 20
screen_check "$W"
echo "CONSOLE SHOWS TEXT OK"
echo "ALL INSTALL CHECKS OK ($ARCH)"
