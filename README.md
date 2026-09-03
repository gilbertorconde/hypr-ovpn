# hypr-ovpn

A small tray-based OpenVPN client for Hyprland / Wayland desktops, modeled on
the Android OpenVPN client: import a `.ovpn` file, set a username and an
optional password, and the VPN becomes a one-click entry in the tray menu.

## Features

- **Tray icon** (StatusNotifierItem — works with waybar's `tray` module) with
  color-coded state: gray = disconnected, blinking amber = connecting,
  green = connected. Left-click opens settings, right-click the menu.
- **Fast connect** from the tray menu; picking another VPN while connected
  switches cleanly (disconnect, then connect). One VPN at a time.
- **Profiles** like the Android client: name, `.ovpn` config (chosen with the
  native system file picker via xdg-desktop-portal), username, optional
  password. Self-contained configs are imported (copied) so the original file
  can be moved or deleted; configs referencing external `ca`/`cert`/`key`
  files are used in place and you're warned about it.
- **Password handling**: if saved, it goes into the **system keyring**
  (gnome-keyring via `secret-tool`), never plaintext on disk. If not saved,
  you're prompted on every connect (with a "save it" checkbox). Wrong
  password → OpenVPN is held up and you're re-prompted, no full restart.
  Private-key passphrases and other auth types are prompted for too.
- **Privilege handling**: OpenVPN runs as root via `pkexec` (one polkit prompt
  per connect); the app itself stays unprivileged and controls OpenVPN through
  a password-protected management socket on localhost — including disconnect,
  which needs **no** second root prompt.
- **Extras**: desktop notifications on connect/disconnect/auth-failure, live
  log viewer, ↓/↑ traffic counters in the tooltip, per-profile
  connect-on-startup, single instance, CLI control.

## Requirements

All in the standard Arch repos: `openvpn`, `python-pyqt6`, `polkit` (with a
running polkit agent, e.g. `polkit-gnome` or `hyprpolkitagent`),
`xdg-desktop-portal` + a FileChooser backend (`xdg-desktop-portal-gtk`), and
for keyring storage `libsecret` + a Secret Service (gnome-keyring, KeePassXC…).

## Install

```sh
./install.sh            # symlinks to ~/.local/bin, installs the .desktop file
./install.sh --polkit   # + optional: polkit policy so repeated VPN starts
                        #   within a few minutes only ask for your password once
```

Autostart with Hyprland (`~/.config/hypr/hyprland.conf`):

```
exec-once = ~/.local/bin/hypr-ovpn
```

## Usage

Run `hypr-ovpn` — the padlock appears in the tray. Add VPNs via
*Settings… → Add…*. Running it again focuses the settings window.

CLI control of the running instance (handy for Hyprland keybinds):

```sh
hypr-ovpn --toggle Work        # connect/disconnect the profile named "Work"
hypr-ovpn --connect Work
hypr-ovpn --disconnect
hypr-ovpn --status
hypr-ovpn --settings
```

e.g. `bind = $mainMod, V, exec, hypr-ovpn --toggle Work`

## Notes

- **DNS**: OpenVPN 2.7 applies pushed DNS servers through its bundled
  `dns-updown` script (systemd-resolved aware). If DNS doesn't switch after
  connecting, check `resolvectl status` and your resolver setup.
- Configs are stored under `~/.config/hypr-ovpn/`; imported `.ovpn` files are
  chmod 600.
- The management socket uses a random per-connection password kept in a 0600
  tempfile, and credentials are passed to OpenVPN only over that socket —
  never on the command line.
