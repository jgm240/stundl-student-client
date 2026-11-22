# SSC Server (ISO)

A small Debian 12 system that runs Stundl Student Client as a server for your home network. Ships as a bootable
ISO for 64-bit (`amd64`) and 32-bit (`i386`, 686-pae) PCs. It can run live from a USB stick or be installed to disk.

* **Accounts with three roles** - *guest* and *user* see the Parent view (the admin chooses which sections each
  role may see); *admin* gets the full SSC app plus an **Admin panel** (accounts, network, web and link settings, SSH, WebUntis key, activity log).
* **`ssconfig`** on the console configures everything: `ssconfig` (menu), `ssconfig showip`, `ssconfig showlink`,
  `ssconfig set links.long true` ("require long link"), `ssconfig ip 192.168.1.50/24 192.168.1.1` (custom IP address), `ssconfig status`.
* **WebUntis without a browser**: the server signs in with an *Untis Mobile* key (QR text) - no password is stored.
* No passwords stored (only salted hashes), sign-in is throttled, sessions are HttpOnly cookies, the firewall allows only the web, link and SSH ports.

## Install
1. Download `ssc-server-<version>-amd64.iso` (or `-i386.iso` for old 32-bit PCs) and check it against the `.sha256` file.
2. Write it to a USB stick (balenaEtcher, `dd`) or attach it to a virtual machine.
3. Boot it and choose **Install SSC Server to disk**. The text installer asks for the disk (everything on it is erased), a
   host name, the administrator name + password, network (DHCP or fixed address), time zone and keyboard.
4. Restart without the USB stick. The login screen shows the web address. On the console run `sudo ssconfig showlink` to get the Parent link.
5. Open `http://<address>:8080/`, sign in as the administrator, open **Admin panel -> WebUntis** and paste the Untis Mobile QR text
   (WebUntis > Profile > Access > *Untis Mobile*).

**Unattended install (`autoinstall.ssci`):** put a plain-text file called `autoinstall.ssci` on a USB stick - or any attached drive - and boot the machine from the installer. It finds the file by itself (it looks at every attached drive, read-only), and installs with the settings in it. **Settings you leave out are asked on screen, like in the normal installer**; add `ask = no` to use defaults instead, fully unattended. Make the file with `sscigen` (below) or copy `autoinstall.ssci.example` from the installer stick.
* **Safety:** the disk is only erased if the file says `erase = yes` (or you type ERASE when it asks); the drive the file came from is never erased; there is a countdown (`wait = 15` seconds) before an unattended install changes anything - power off to cancel; more than one file on different drives is an error; add `ssc.noautoinstall` to the kernel line to switch the feature off.
* **It's data, not code:** the file is plain `key = value` text (JSON works too). Nothing in it is executed, unknown settings are errors with the line number, and everything is checked before a disk is touched.
* **It can set**: the installer questions (disk, hostname, admin, password, time zone, keyboard, SSH, auto-login, network/static IP, Wi-Fi), extra accounts (`account.anna = user:password`), the WebUntis key (`untis = untis://...`), and any server setting as `section.key = value` (`power.lid`, `schedule.enabled`, `links.long`, ...). The file holds passwords in clear text: keep the stick private.

## sscigen - write an `autoinstall.ssci` by answering the installer's questions
`sscigen` (on the installer, or `./sscigen` from the repository - also on a Mac or in WSL) asks the same questions as the installer, with the same checks, and writes the file instead of installing: `sscigen -o /Volumes/USB/autoinstall.ssci`. `sscigen --edit old.ssci` keeps what a file already says and asks only the rest, `sscigen --check file.ssci` tells you whether the installer will accept a file. With `whiptail` installed it looks like the installer; otherwise it uses plain prompts.

## Old laptops as servers (lid closed)
By default closing the lid does **nothing** - the machine keeps running - and it never suspends or hibernates (the sleep keys and idle timeouts are ignored too). This is the default of the image (live and installed).
* **Console:** `ssconfig lid status|ignore|suspend|poweroff` and `ssconfig sleep block|allow`, or "Laptop lid / sleep" in the `ssconfig` menu.
* **Admin panel:** the Power tab.
* It's done with systemd-logind (`/etc/systemd/logind.conf.d/ssc-power.conf`) and by masking the sleep targets.
* With the lid closed a laptop gets warm: keep the vents free and plug it in. Whether the screen turns off or the backlight goes dark is up to the laptop. Not tested on a real laptop lid (QEMU has none), only the generated settings are tested.

## Sleep schedule (night mode)
`ssconfig schedule set 22:00 06:00` (or the Power tab) switches the server off at night and back on in the morning, on the days you choose.
* **Quiet** (default): stops the SSC services (web, Parent links, sync - and SSH if you ask), spins down the *rotating* hard disks (`hdparm -y`; SSDs have nothing to spin down) and blanks the console. The machine itself keeps running. In the morning everything starts again. The web page is unreachable during the night.
* **Suspend** / **Power off**: the machine sleeps and wakes up by itself at the switch-on time through the hardware clock (RTC). Needs a PC or laptop that supports it (a BIOS "wake on RTC alarm" setting may need to be on); without an RTC only "quiet" is accepted.
* `ssconfig schedule up` ends a sleep period by hand; a machine that is started inside the sleep period in quiet mode goes back to sleep. Times are the server's local time.
* Not tested on real hardware: the planning, the generated systemd timers and the commands are unit-tested, but actual disk spin-down, screen blanking and RTC wake-up need a real machine.

## Updates
The Overview tab of the admin panel (or `ssconfig update`, `ssconfig update --install`, `ssconfig update --rollback`) updates SSC Server from this project's GitHub releases. It only happens when you ask. The new code is unpacked into a staging folder (only the known SSC files, nothing else), every Python file is compile-checked, the new version must import and report the version of the release, and only then is it swapped in; the old version is kept in `/opt/ssc.prev` and the web service restarts. This updates SSC itself, not Debian (use `apt` for that). Honest limit: a source archive has no signature or checksum, so the update trusts GitHub's TLS and the project's repository.

## Wi-Fi
The image includes `wpa_supplicant` and firmware for common Wi-Fi chips (Intel, Atheros, Realtek, Broadcom, Ralink; Debian's
`non-free-firmware` packages, redistributed under their own licences). Chips that need other firmware won't work out of the box.
* **Console:** `ssconfig wifi scan`, `ssconfig wifi set "My Network" --country=AT` (asks for the password; add `--hidden` or `--open`),
  `ssconfig wifi status`, `ssconfig wifi off` - or the **Wi-Fi** entry in the `ssconfig` menu, which lists networks to pick from.
* **Admin panel:** Network tab -> Wi-Fi. (No network list there: type the name.)
* **Installer:** if a Wi-Fi card is found it offers to connect; unattended installs take `"wifi":{"ssid":"..","passphrase":"..","country":"AT"}`.
* With Wi-Fi on, the address settings (DHCP or fixed IP) apply to the Wi-Fi interface and a network cable, if plugged in, uses DHCP.
* The password is never stored: only the derived WPA2 key goes into a root-only file (`/etc/wpa_supplicant/ssc-wifi.conf`).
  Set the country code, or 5 GHz networks may not be found. WPA2-Personal and open networks only (no WPA3-only or enterprise/802.1X).

## Access cards
`ssc-imgen --user anna --notes "Sign in on the TV" -o anna.png` makes a printable card (logo, the server's `ip:port`, user name, password, notes, QR code) to hand to a family member - like a hotel Wi-Fi card. The address is detected from this server's settings. `--lang de` (12 languages) changes the card's language, `--network "Home WiFi"` shows the network name to join, and `--cellularwarn` adds a notice to use WiFi instead of mobile data. The password is typed at a prompt (it can't be read back from the server, which only keeps hashes) and appears only on the picture: delete the file after printing.

## Notes and limits
* Beta software. The ISOs are tested in QEMU (boot, unattended install, first boot, web login), not on a wide range of real hardware.
* Secure Boot is not supported (the ISO is not signed with a Microsoft-trusted key); turn it off for the install.
* The WebUntis mobile interface is undocumented and can change; the server then just keeps showing its last data.
* Wi-Fi is covered by unit tests (key derivation against the IEEE test vector, generated files, validation) but the CI tests run without a Wi-Fi card, so it has **not** been tried on real Wi-Fi hardware.
* The live medium has no SSH and no network services configured until you set it up or install.
* HTTPS: put `cert.pem` and `key.pem` in `/etc/ssc/tls/`, then `ssconfig set web.https true`.
