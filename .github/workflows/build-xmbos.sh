#!/usr/bin/env bash
# Builds XMBOS: a live + installer ISO that boots straight into xmb.py.
# Run as root on Arch (needs: pacman -S archiso). Keep xmb.py next to this script.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run as root on an Arch system."; exit 1; }
HERE=$(cd "$(dirname "$0")" && pwd)
[[ -f $HERE/xmb.py ]] || { echo "xmb.py must be next to this script."; exit 1; }

PKGS=(cage python-pygame foot noto-fonts retroarch libretro-core-info mpv
      networkmanager pipewire pipewire-pulse wireplumber mesa vulkan-icd-loader
      vulkan-radeon vulkan-intel sudo firefox)

P=$HERE/xmb-iso
A=$P/airootfs
rm -rf "$P" "$HERE/work"
cp -r /usr/share/archiso/configs/releng "$P"

# --- profile -----------------------------------------------------------
sed -i 's/^iso_name=.*/iso_name="xmbos"/; s/^iso_label=.*/iso_label="XMBOS"/' "$P/profiledef.sh"
printf '%s\n' "${PKGS[@]}" >> "$P/packages.x86_64"
cat >> "$P/profiledef.sh" <<'EOF'
file_permissions+=(
  ["/usr/local/bin/xmb-install"]="0:0:755"
  ["/etc/sudoers.d/xmb"]="0:0:440"
)
EOF

mkdir -p "$A/opt/xmb" "$A/usr/local/bin" "$A/usr/local/share/xmb" \
         "$A/etc/skel/.config/xmb" "$A/etc/sudoers.d" \
         "$A/etc/systemd/system/getty@tty1.service.d" \
         "$A/etc/systemd/system/multi-user.target.wants"

cp "$HERE/xmb.py" "$A/opt/xmb/xmb.py"
printf '%s\n' "${PKGS[@]}" > "$A/usr/local/share/xmb/packages"

# --- live user + autologin straight into the XMB ------------------------
cat > "$A/etc/systemd/system/xmb-user.service" <<'EOF'
[Unit]
Description=Create live xmb user
Before=getty@tty1.service
ConditionPathExists=!/home/xmb
[Service]
Type=oneshot
ExecStart=/usr/bin/useradd -m -G wheel,video,input,audio,render -s /bin/bash xmb
ExecStart=/usr/bin/passwd -d xmb
[Install]
WantedBy=multi-user.target
EOF
ln -sf /etc/systemd/system/xmb-user.service "$A/etc/systemd/system/multi-user.target.wants/xmb-user.service"
ln -sf /usr/lib/systemd/system/NetworkManager.service "$A/etc/systemd/system/multi-user.target.wants/NetworkManager.service"

cat > "$A/etc/systemd/system/getty@tty1.service.d/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --noreset --noclear --autologin xmb - ${TERM}
EOF
echo 'xmb ALL=(ALL) NOPASSWD: ALL' > "$A/etc/sudoers.d/xmb"

cat > "$A/etc/skel/.bash_profile" <<'EOF'
if [[ -z ${WAYLAND_DISPLAY:-} && ${XDG_VTNR:-0} -eq 1 ]]; then
  export SDL_VIDEODRIVER=wayland WLR_RENDERER_ALLOW_SOFTWARE=1 BROWSER=firefox XMB_BROWSER=firefox
  exec cage -s -- python3 /opt/xmb/xmb.py
fi
EOF

cat > "$A/etc/skel/.config/xmb/config.json" <<'EOF'
[
  {"name": "Settings", "items": [
    {"name": "Install to Disk", "cmd": "foot bash -c 'xmb-install; read -rp \"Press Enter\" _'"},
    {"name": "Wi-Fi", "cmd": "foot nmtui"},
    {"name": "Terminal", "cmd": "foot"},
    {"name": "Reboot", "cmd": "systemctl reboot", "confirm": true},
    {"name": "Shut Down", "cmd": "systemctl poweroff", "confirm": true}]},
  {"name": "Games", "items": [
    {"name": "RetroArch", "cmd": "retroarch"}]},
  {"name": "Media", "items": [
    {"name": "Videos", "cmd": "mpv --fs ~/Videos"},
    {"name": "Music", "cmd": "mpv --no-video ~/Music"}]},
  {"name": "News", "feeds": [
    "https://hnrss.org/frontpage",
    "https://hackaday.com/blog/feed/",
    "https://feeds.bbci.co.uk/news/rss.xml"]}
]
EOF

# --- installer (UEFI, wipes a whole disk, systemd-boot) ------------------
cat > "$A/usr/local/bin/xmb-install" <<'INSTALLER'
#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
[[ -d /sys/firmware/efi ]] || { echo "UEFI boot required."; exit 1; }

echo "Disks:"; lsblk -dpno NAME,SIZE,MODEL -e 7,11
read -rp "Install to which disk (e.g. /dev/sda)? " D
[[ -b $D ]] || { echo "Not a block device."; exit 1; }
read -rp "ALL DATA on $D will be erased. Type YES: " ok
[[ $ok == YES ]] || { echo "Cancelled."; exit 1; }
read -rp "Username [player]: " U; U=${U:-player}
[[ $U =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Bad username."; exit 1; }
read -rp "Hostname [xmbos]: " H; H=${H:-xmbos}
read -rsp "Password: " PW; echo
[[ -n $PW ]] || { echo "Empty password."; exit 1; }

sep=""; [[ $D =~ [0-9]$ ]] && sep="p"
umount -R /mnt 2>/dev/null || true
wipefs -af "$D"; sgdisk -Z "$D"
sgdisk -n1:0:+512M -t1:ef00 -n2:0:0 -t2:8300 "$D"
partprobe "$D"; sleep 2
mkfs.fat -F32 "${D}${sep}1"; mkfs.ext4 -qF "${D}${sep}2"
mount "${D}${sep}2" /mnt
mount -o umask=0077 --mkdir "${D}${sep}1" /mnt/boot

pacstrap -K /mnt base linux linux-firmware amd-ucode intel-ucode $(cat /usr/local/share/xmb/packages)
genfstab -U /mnt >> /mnt/etc/fstab

arch-chroot /mnt bash -s -- "$U" "$H" <<'CH'
set -e
U=$1; H=$2
ln -sf /usr/share/zoneinfo/UTC /etc/localtime
sed -i 's/^#en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen; locale-gen
echo LANG=en_US.UTF-8 > /etc/locale.conf
echo "$H" > /etc/hostname
useradd -m -G wheel,video,input,audio,render -s /bin/bash "$U"
echo '%wheel ALL=(ALL:ALL) ALL' > /etc/sudoers.d/10-wheel
systemctl enable NetworkManager
bootctl install
CH
printf '%s:%s\n' "$U" "$PW" | arch-chroot /mnt chpasswd

# XMB session for the new user
cp -a /opt/xmb /mnt/opt/
cp -a /etc/skel/. "/mnt/home/$U/"
python3 - "/mnt/home/$U/.config/xmb/config.json" <<'PY'
import json, sys
p = sys.argv[1]; c = json.load(open(p))
for cat in c:
    cat["items"] = [i for i in cat["items"] if i["name"] != "Install to Disk"]
json.dump(c, open(p, "w"), indent=2)
PY
arch-chroot /mnt chown -R "$U:$U" "/home/$U"

mkdir -p /mnt/etc/systemd/system/getty@tty1.service.d
cat > /mnt/etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --noreset --noclear --autologin $U - \${TERM}
EOF

RU=$(blkid -s UUID -o value "${D}${sep}2")
cat > /mnt/boot/loader/loader.conf <<EOF
default xmbos.conf
timeout 2
EOF
mkdir -p /mnt/boot/loader/entries
cat > /mnt/boot/loader/entries/xmbos.conf <<EOF
title XMBOS
linux /vmlinuz-linux
initrd /amd-ucode.img
initrd /intel-ucode.img
initrd /initramfs-linux.img
options root=UUID=$RU rw quiet
EOF

umount -R /mnt
echo "Done. Remove the USB stick and reboot."
INSTALLER

# --- build ---------------------------------------------------------------
mkarchiso -v -w "$HERE/work" -o "$HERE/out" "$P"
echo "ISO: $(ls "$HERE"/out/xmbos-*.iso)"
