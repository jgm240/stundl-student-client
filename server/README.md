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

**Unattended install:** attach a small disk labelled `SSCANS` containing `answers.json`
(`{"disk":"auto","hostname":"ssc","admin":"admin","password":"...","timezone":"UTC","keymap":"us","ssh":true,"network":{"mode":"dhcp"}}`);
the installer runs by itself and powers off when done.

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

## Notes and limits
* Beta software. The ISOs are tested in QEMU (boot, unattended install, first boot, web login), not on a wide range of real hardware.
* Secure Boot is not supported (the ISO is not signed with a Microsoft-trusted key); turn it off for the install.
* The WebUntis mobile interface is undocumented and can change; the server then just keeps showing its last data.
* Wi-Fi is covered by unit tests (key derivation against the IEEE test vector, generated files, validation) but the CI tests run without a Wi-Fi card, so it has **not** been tried on real Wi-Fi hardware.
* The live medium has no SSH and no network services configured until you set it up or install.
* HTTPS: put `cert.pem` and `key.pem` in `/etc/ssc/tls/`, then `ssconfig set web.https true`.
