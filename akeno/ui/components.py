"""Component controls using the existing suite design system."""
from PySide6.QtWidgets import QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.widgets import Card, FlowLayout, KeyValueGrid, StatusPill, button, info, label


class ComponentUnavailablePage(QWidget):
    """An absent optional component is a supported state, never a failed updater health check."""
    def __init__(self, status):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addWidget(label(t("Security Center"), "h2"))
        lay.addWidget(label(t("Security Center is disabled or unavailable. Manage it in Settings → Components."),
                            "dim", wrap=True))
        if status.get("detail"):
            lay.addWidget(label(status["detail"], "muted", wrap=True))
        lay.addStretch(1)


def wireguard_detected() -> bool | None:
    """Whether the official, signed WireGuard for Windows is installed (None: not a Windows build)."""
    import sys
    if sys.platform != "win32":
        return None
    from akeno.network.windows import NativeUnavailable, WindowsWireGuard
    try:
        WindowsWireGuard()._trusted("wireguard.exe")
        return True
    except NativeUnavailable:
        return False


class ComponentCard(Card):
    def __init__(self, services, window):
        super().__init__(t("Components"), "layers")
        self.services, self.window = services, window
        self.body.addWidget(label(t("Security Center is optional. Other suite features currently share the Core "
                                   "bundle. AKENO installs no network adapter automatically."), "dim", wrap=True))
        self.overview = KeyValueGrid(170)
        self.body.addWidget(self.overview)
        self.status_label = label("", "muted", wrap=True)
        self.body.addWidget(self.status_label)
        self.toggle = button("", "shield", on_click=self.change)
        self.body.addWidget(self.toggle)
        actions = FlowLayout(spacing=8)
        self.operations = {}
        for action, text in (("install", "Install Security Center"), ("repair", "Repair Security Center"),
                             ("remove", "Remove Security Center")):
            b = button(t(text), "download" if action != "remove" else "trash",
                       on_click=lambda _=False, a=action: self.operate(a))
            self.operations[action] = b
            actions.addWidget(b)
        self.body.addLayout(actions)
        net = FlowLayout(spacing=8)
        self.network_buttons = {}
        for module in ("lan_play", "vpn"):
            b = button("", "power", on_click=lambda _=False, m=module: self.toggle_network(m))
            self.network_buttons[module] = b
            net.addWidget(b)
        self.body.addLayout(net)
        self.body.addWidget(label(t("Component changes use the signed package for this AKENO version and restart "
                                   "the application. The previous installation is kept for rollback."), "muted", wrap=True))
        self.refresh()

    def refresh(self):
        state = self.services.components.status()["state"]
        names = {"active": t("Active"), "disabled": t("Disabled"), "not_installed": t("Not installed"),
                 "error": t("Error"), "incompatible": t("Incompatible")}
        pills = {"active": "ok", "disabled": "unknown", "not_installed": "unknown", "error": "fail",
                 "incompatible": "fail"}
        installed = state not in ("not_installed",)
        wireguard = wireguard_detected()
        from akeno.components.service import NetworkModules
        network = NetworkModules().status()
        names_net = {"lan_play": t("LAN Play"), "vpn": t("VPN")}
        for module, b in self.network_buttons.items():
            b.setText((t("Turn off {name}") if network[module] else t("Turn on {name}")).format(name=names_net[module]))
        self.overview.set_rows([
            (t("AKENO Core"), StatusPill(t("Installed · always active"), "ok")),
            (t("Security Center"), StatusPill((t("Installed") if installed else t("Not installed")) + " · " +
                                              names[state], pills[state])),
            (t("Requires"), t("Microsoft Defender and Windows Security Center (part of Windows)")),
            (t("Permissions"), t("Your user rights; scans are run by Microsoft Defender itself")),
            (t("AKENO LAN Play"), StatusPill(t("On") if network["lan_play"] else t("Off"),
                                              "ok" if network["lan_play"] else "unknown")),
            (t("AKENO VPN"), StatusPill(t("On") if network["vpn"] else t("Off"), "ok" if network["vpn"] else "unknown")),
            (t("Packaging"), t("Part of Core; switched off until you turn it on. Updates never switch it on.")),
            (t("Requires"), t("Official WireGuard for Windows (not installed by AKENO)") + " — " +
             (t("detected") if wireguard else t("not detected") if wireguard is False else t("Windows only"))),
            (t("Permissions"), t("Administrator rights once per LAN session or VPN action (Windows asks)")),
        ])
        self.status_label.setText(t("Security Center") + ": " + names[state])
        self.toggle.setText(t("Disable Security Center") if state == "active" else t("Enable Security Center"))
        self.toggle.setEnabled(state in ("active", "disabled"))
        supported = self.services.components.frozen and self.window is not None
        for action, b in self.operations.items():
            b.setEnabled(supported and (state == "not_installed" if action == "install" else state != "not_installed"))

    def toggle_network(self, module):
        from akeno.components.service import NetworkModules
        modules = NetworkModules()
        modules.set_enabled(module, not modules.enabled(module))
        page = self.window.pages.get("akeno_network") if self.window is not None else None
        if page is not None:
            page.show_modules()
        self.refresh()

    def operate(self, action):
        if self.window is None or any(k.startswith("security:") for k in self.window.ctl.busy):
            info(self, t("Components"), t("Wait for the Security Center task to finish or cancel it first."))
            return
        self.window.go("updates")
        self.window.pages["updates"].change_component(action)

    def change(self):
        from akeno.components.service import ComponentError
        if self.window is not None and any(key.startswith("security:") for key in self.window.ctl.busy):
            info(self, t("Components"), t("Wait for the Security Center task to finish or cancel it first."))
            return
        try:
            self.services.components.set_enabled(not self.services.components.active())
            if self.window is not None:
                self.window.refresh_security_component()
        except (ComponentError, OSError) as exc:
            info(self, t("Components"), str(exc), error=True)
        self.refresh()
