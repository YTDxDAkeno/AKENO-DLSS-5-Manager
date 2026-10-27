"""Component controls using the existing suite design system."""
from PySide6.QtWidgets import QVBoxLayout, QWidget

from akeno.i18n import t
from akeno.ui.widgets import Card, button, info, label


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


class ComponentCard(Card):
    def __init__(self, services, window):
        super().__init__(t("Components"), "layers")
        self.services, self.window = services, window
        self.body.addWidget(label(t("Security Center is optional. Other suite features currently share the Core "
                                   "bundle. LAN Play and VPN are not installed by this build."), "dim", wrap=True))
        self.status_label = label("", "muted", wrap=True)
        self.body.addWidget(self.status_label)
        self.toggle = button("", "shield", on_click=self.change)
        self.body.addWidget(self.toggle)
        self.body.addWidget(label(t("To add or remove Security Center files, run the AKENO installer and change "
                                   "its component selection. Separate module downloads are not available yet."),
                                 "muted", wrap=True))
        self.refresh()

    def refresh(self):
        state = self.services.components.status()["state"]
        names = {"active": t("Active"), "disabled": t("Disabled"), "not_installed": t("Not installed"),
                 "error": t("Error"), "incompatible": t("Incompatible")}
        self.status_label.setText(t("Security Center") + ": " + names[state])
        self.toggle.setText(t("Disable Security Center") if state == "active" else t("Enable Security Center"))
        self.toggle.setEnabled(state in ("active", "disabled"))

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
