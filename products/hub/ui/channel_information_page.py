from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from products.hub.twitch.channel_information import (
    SOCIAL_SERVICES,
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
    normalize_multiline_text,
    normalize_social_url,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore


class ChannelInformationPage(QWidget):
    saved = Signal()

    def __init__(
        self,
        store: ChannelInformationStore,
        command_store: TwitchCommandTriggerStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.command_store = command_store
        self.social_rows: dict[str, tuple[QCheckBox, QLineEdit, QLabel]] = {}
        self._enable_default_id = ""
        self._loading = False
        self._build_ui()
        self.load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Reusable channel details for Twitch commands and automation routines. "
            "Links remain saved even when they are not included in !socials."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        social_group = QGroupBox("Social Links")
        social_layout = QVBoxLayout(social_group)
        social_help = QLabel("Choose which valid links are included in !socials.")
        social_help.setWordWrap(True)
        social_layout.addWidget(social_help)
        grid = QGridLayout()
        grid.addWidget(QLabel("Include"), 0, 0)
        grid.addWidget(QLabel("Service"), 0, 1)
        grid.addWidget(QLabel("Link"), 0, 2)
        for row, (service_id, label) in enumerate(SOCIAL_SERVICES, start=1):
            include = QCheckBox()
            include.setObjectName(f"channelInformationInclude{service_id.title()}")
            edit = QLineEdit()
            edit.setObjectName(f"channelInformation{service_id.title()}Url")
            edit.setPlaceholderText("https://")
            error = QLabel()
            error.setObjectName(f"channelInformation{service_id.title()}Error")
            error.setStyleSheet("color: #e5a84b;")
            error.setWordWrap(True)
            grid.addWidget(include, row, 0)
            grid.addWidget(QLabel(label), row, 1)
            grid.addWidget(edit, row, 2)
            grid.addWidget(error, row, 3)
            self.social_rows[service_id] = include, edit, error
            include.toggled.connect(self._values_changed)
            edit.textChanged.connect(self._values_changed)
        grid.setColumnStretch(2, 1)
        social_layout.addLayout(grid)
        preview_title = QLabel("!socials preview")
        preview_title.setStyleSheet("font-weight: 600;")
        social_layout.addWidget(preview_title)
        self.socials_preview_label = QLabel()
        self.socials_preview_label.setObjectName("channelInformationSocialsPreview")
        self.socials_preview_label.setWordWrap(True)
        social_layout.addWidget(self.socials_preview_label)
        layout.addWidget(social_group)

        other_group = QGroupBox("Other Channel Information")
        other_layout = QGridLayout(other_group)
        self.schedule_edit = self._multiline_editor(
            "channelInformationSchedule", "Stream times or a schedule URL"
        )
        self.rules_edit = self._multiline_editor(
            "channelInformationRules", "Channel rules or a rules URL"
        )
        self.server_info_edit = self._multiline_editor(
            "channelInformationServer", "Server name, address, or joining instructions"
        )
        for row, (label, editor, dependency) in enumerate(
            (
                ("Schedule", self.schedule_edit, "Used by !schedule"),
                ("Rules", self.rules_edit, "Used by !rules"),
                ("Server Information", self.server_info_edit, "Used by !server"),
            )
        ):
            heading = QLabel(f"{label}\n{dependency}")
            heading.setWordWrap(True)
            other_layout.addWidget(heading, row, 0)
            other_layout.addWidget(editor, row, 1)
            editor.textChanged.connect(self._values_changed)
        other_layout.setColumnStretch(1, 1)
        layout.addWidget(other_group)

        self.enable_after_saving_check = QCheckBox()
        self.enable_after_saving_check.setObjectName(
            "channelInformationEnableAfterSaving"
        )
        self.enable_after_saving_check.hide()
        layout.addWidget(self.enable_after_saving_check)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save Changes")
        self.save_button.setObjectName("channelInformationSave")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        actions.addWidget(self.save_button)
        actions.addWidget(self.status_label, 1)
        layout.addLayout(actions)
        layout.addStretch()
        self.save_button.clicked.connect(self.save_values)

    @staticmethod
    def _multiline_editor(object_name: str, placeholder: str) -> QTextEdit:
        editor = QTextEdit()
        editor.setObjectName(object_name)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText(placeholder)
        editor.setMaximumHeight(90)
        return editor

    def load_values(self) -> None:
        self._loading = True
        information = self.store.snapshot()
        for service_id, (include, edit, error) in self.social_rows.items():
            link = information.social_links[service_id]
            include.setChecked(link.enabled_in_socials)
            edit.setText(link.url)
            error.clear()
        self.schedule_edit.setPlainText(information.schedule)
        self.rules_edit.setPlainText(information.rules)
        self.server_info_edit.setPlainText(information.server_info)
        self._loading = False
        self.save_button.setEnabled(False)
        self.status_label.clear()
        self._refresh_preview()

    def _values_changed(self, *_args) -> None:
        if self._loading:
            return
        self.save_button.setEnabled(True)
        self.status_label.setText("Unsaved changes")
        self._refresh_preview()

    def _candidate(self, *, show_errors: bool) -> ChannelInformation | None:
        links: dict[str, SocialLink] = {}
        valid = True
        for service_id, (include, edit, error) in self.social_rows.items():
            try:
                url = normalize_social_url(edit.text())
                message = ""
                if include.isChecked() and not url:
                    message = "Add a link or uncheck Include."
                    valid = False
            except ValueError as validation_error:
                url = ""
                message = str(validation_error)
                valid = False
            if show_errors or message:
                error.setText(message)
            links[service_id] = SocialLink(include.isChecked(), url)
        if not valid:
            return None
        return ChannelInformation(
            social_links=links,
            schedule=normalize_multiline_text(self.schedule_edit.toPlainText()),
            rules=normalize_multiline_text(self.rules_edit.toPlainText()),
            server_info=normalize_multiline_text(self.server_info_edit.toPlainText()),
        )

    def _refresh_preview(self) -> None:
        parts: list[str] = []
        seen: set[str] = set()
        for service_id, label in SOCIAL_SERVICES:
            include, edit, _error = self.social_rows[service_id]
            if not include.isChecked():
                continue
            try:
                url = normalize_social_url(edit.text())
            except ValueError:
                continue
            key = url.casefold().rstrip("/")
            if not url or key in seen:
                continue
            candidate = " | ".join((*parts, f"{label}: {url}"))
            if len(candidate) > 480:
                break
            seen.add(key)
            parts.append(f"{label}: {url}")
        self.socials_preview_label.setText(
            " | ".join(parts)
            if parts
            else "Setup Required — select at least one valid social link."
        )

    def save_values(self) -> None:
        candidate = self._candidate(show_errors=True)
        if candidate is None:
            self.status_label.setText("Fix the highlighted social links before saving.")
            return
        try:
            self.store.save(candidate)
        except (OSError, ValueError) as error:
            self.status_label.setText(f"Could not save Channel Information: {error}")
            return
        enabled = False
        if (
            self._enable_default_id
            and self.enable_after_saving_check.isChecked()
            and self.command_store is not None
        ):
            command = self.command_store.default(self._enable_default_id)
            requirement = self.command_store.setup_requirement(
                self._enable_default_id
            )
            ready = (
                bool(self.store.usable_social_links())
                if requirement == "socials"
                else self.store.field_available(requirement)
            )
            if ready:
                command = command or self.command_store.configure_default(
                    self._enable_default_id
                )
                self.command_store.set_enabled(command.trigger_id, True)
                enabled = True
        self.load_values()
        self.status_label.setText(
            "Channel Information saved and command enabled."
            if enabled
            else "Channel Information saved. Commands remain disabled until you enable them."
        )
        self.saved.emit()

    def focus_for_command(self, default_id: str) -> None:
        requirement = TwitchCommandTriggerStore.setup_requirement(default_id)
        self._enable_default_id = default_id if requirement else ""
        command = self.command_store.default(default_id) if self.command_store else None
        self.enable_after_saving_check.setVisible(
            bool(requirement and (command is None or not command.enabled))
        )
        self.enable_after_saving_check.setChecked(False)
        self.enable_after_saving_check.setText(
            f"Configure and enable !{default_id} after saving"
            if command is None
            else f"Enable !{command.name} after saving"
        )
        if requirement in self.social_rows:
            self.social_rows[requirement][1].setFocus()
        elif requirement == "socials":
            self.social_rows["discord"][1].setFocus()
        elif requirement == "schedule":
            self.schedule_edit.setFocus()
        elif requirement == "rules":
            self.rules_edit.setFocus()
        elif requirement == "server_info":
            self.server_info_edit.setFocus()
