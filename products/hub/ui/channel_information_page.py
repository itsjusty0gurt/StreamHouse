from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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

    _COMPACT_WIDTH = 720
    _COMPACT_HYSTERESIS = 24

    def __init__(
        self,
        store: ChannelInformationStore,
        command_store: TwitchCommandTriggerStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.command_store = command_store
        self.social_rows: dict[
            str, tuple[QCheckBox, QLineEdit, QCheckBox, QLabel]
        ] = {}
        self._social_labels: dict[str, QLabel] = {}
        self.other_expose_checks: dict[str, QCheckBox] = {}
        self._other_headings: dict[str, QLabel] = {}
        self._compact_layout = False
        self._enable_default_id = ""
        self._loading = False
        self._build_ui()
        self.load_values()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("channelInformationScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        outer_layout.addWidget(self.scroll_area)

        self.content_widget = QWidget(self.scroll_area)
        self.content_widget.setObjectName("channelInformationContent")
        self.scroll_area.setWidget(self.content_widget)
        layout = QVBoxLayout(self.content_widget)
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
        self.social_grid = QGridLayout()
        self.social_headers = (
            QLabel("Include"),
            QLabel("Service"),
            QLabel("Link"),
            QLabel("Expose as Variable"),
        )
        for row, (service_id, label) in enumerate(SOCIAL_SERVICES, start=1):
            include = QCheckBox()
            include.setObjectName(f"channelInformationInclude{service_id.title()}")
            edit = QLineEdit()
            edit.setObjectName(f"channelInformation{service_id.title()}Url")
            edit.setPlaceholderText("https://")
            expose = QCheckBox()
            expose.setObjectName(f"channelInformationExpose{service_id.title()}")
            error = QLabel()
            error.setObjectName(f"channelInformation{service_id.title()}Error")
            error.setStyleSheet("color: #e5a84b;")
            error.setWordWrap(True)
            service_label = QLabel(label)
            service_label.setWordWrap(True)
            self._social_labels[service_id] = service_label
            self.social_rows[service_id] = include, edit, expose, error
            include.toggled.connect(self._values_changed)
            edit.textChanged.connect(self._values_changed)
            expose.toggled.connect(self._values_changed)
        social_layout.addLayout(self.social_grid)
        preview_title = QLabel("!socials preview")
        preview_title.setStyleSheet("font-weight: 600;")
        social_layout.addWidget(preview_title)
        self.socials_preview_label = QLabel()
        self.socials_preview_label.setObjectName("channelInformationSocialsPreview")
        self.socials_preview_label.setWordWrap(True)
        social_layout.addWidget(self.socials_preview_label)
        layout.addWidget(social_group)

        other_group = QGroupBox("Other Channel Information")
        self.other_layout = QGridLayout(other_group)
        self.schedule_edit = self._multiline_editor(
            "channelInformationSchedule", "Stream times or a schedule URL"
        )
        self.rules_edit = self._multiline_editor(
            "channelInformationRules", "Channel rules or a rules URL"
        )
        self.server_info_edit = self._multiline_editor(
            "channelInformationServer", "Server name, address, or joining instructions"
        )
        for row, (field_id, label, editor, dependency) in enumerate(
            (
                ("schedule", "Schedule", self.schedule_edit, "Used by !schedule"),
                ("rules", "Rules", self.rules_edit, "Used by !rules"),
                ("server_info", "Server Information", self.server_info_edit, "Used by !server"),
            )
        ):
            heading = QLabel(f"{label}\n{dependency}")
            heading.setWordWrap(True)
            expose = QCheckBox("Expose as Variable")
            expose.setObjectName(
                f"channelInformationExpose{field_id.title().replace('_', '')}"
            )
            self._other_headings[field_id] = heading
            self.other_expose_checks[field_id] = expose
            editor.textChanged.connect(self._values_changed)
            expose.toggled.connect(self._values_changed)
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
        self._apply_responsive_layout(compact=False)

    @staticmethod
    def _multiline_editor(object_name: str, placeholder: str) -> QTextEdit:
        editor = QTextEdit()
        editor.setObjectName(object_name)
        editor.setAcceptRichText(False)
        editor.setPlaceholderText(placeholder)
        editor.setMinimumHeight(72)
        editor.setMaximumHeight(120)
        editor.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        return editor

    @staticmethod
    def _clear_grid(layout: QGridLayout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _apply_responsive_layout(self, *, compact: bool) -> None:
        if compact == self._compact_layout and self.social_grid.count():
            return
        self._compact_layout = compact
        self.setProperty("compactLayout", compact)
        self._clear_grid(self.social_grid)
        self._clear_grid(self.other_layout)

        if compact:
            for header in self.social_headers:
                header.hide()
            for row, (service_id, _label) in enumerate(SOCIAL_SERVICES):
                include, edit, expose, error = self.social_rows[service_id]
                include.setText("Include")
                expose.setText("Expose as Variable")
                base_row = row * 5
                self.social_grid.addWidget(
                    self._social_labels[service_id], base_row, 0, 1, 2
                )
                self.social_grid.addWidget(edit, base_row + 1, 0, 1, 2)
                self.social_grid.addWidget(include, base_row + 2, 0, 1, 2)
                self.social_grid.addWidget(expose, base_row + 3, 0, 1, 2)
                self.social_grid.addWidget(error, base_row + 4, 0, 1, 2)
            self.social_grid.setColumnStretch(0, 1)
            self.social_grid.setColumnStretch(1, 0)

            for row, (field_id, editor) in enumerate(
                (
                    ("schedule", self.schedule_edit),
                    ("rules", self.rules_edit),
                    ("server_info", self.server_info_edit),
                )
            ):
                base_row = row * 3
                self.other_layout.addWidget(
                    self._other_headings[field_id], base_row, 0, 1, 2
                )
                self.other_layout.addWidget(editor, base_row + 1, 0, 1, 2)
                self.other_layout.addWidget(
                    self.other_expose_checks[field_id], base_row + 2, 0, 1, 2
                )
            self.other_layout.setColumnStretch(0, 1)
            self.other_layout.setColumnStretch(1, 0)
            self.content_widget.layout().activate()
            self.content_widget.adjustSize()
            return

        for column, header in enumerate(self.social_headers):
            header.show()
            self.social_grid.addWidget(header, 0, column)
        for row, (service_id, _label) in enumerate(SOCIAL_SERVICES, start=1):
            include, edit, expose, error = self.social_rows[service_id]
            include.setText("")
            expose.setText("")
            self.social_grid.addWidget(include, row, 0)
            self.social_grid.addWidget(self._social_labels[service_id], row, 1)
            self.social_grid.addWidget(edit, row, 2)
            self.social_grid.addWidget(expose, row, 3)
            self.social_grid.addWidget(error, row, 4)
        self.social_grid.setColumnStretch(0, 0)
        self.social_grid.setColumnStretch(1, 0)
        self.social_grid.setColumnStretch(2, 1)
        self.social_grid.setColumnStretch(3, 0)
        self.social_grid.setColumnStretch(4, 0)

        for row, (field_id, editor) in enumerate(
            (
                ("schedule", self.schedule_edit),
                ("rules", self.rules_edit),
                ("server_info", self.server_info_edit),
            )
        ):
            self.other_layout.addWidget(self._other_headings[field_id], row, 0)
            self.other_layout.addWidget(editor, row, 1)
            self.other_layout.addWidget(
                self.other_expose_checks[field_id], row, 2
            )
        self.other_layout.setColumnStretch(0, 0)
        self.other_layout.setColumnStretch(1, 1)
        self.other_layout.setColumnStretch(2, 0)
        self.content_widget.layout().activate()
        self.content_widget.adjustSize()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self.scroll_area.viewport().width()
        if self._compact_layout:
            compact = width < self._COMPACT_WIDTH + self._COMPACT_HYSTERESIS
        else:
            compact = width < self._COMPACT_WIDTH
        self._apply_responsive_layout(compact=compact)

    def load_values(self) -> None:
        self._loading = True
        information = self.store.snapshot()
        for service_id, (include, edit, expose, error) in self.social_rows.items():
            link = information.social_links[service_id]
            include.setChecked(link.enabled_in_socials)
            edit.setText(link.url)
            expose.setChecked(link.expose_as_variable)
            error.clear()
        self.schedule_edit.setPlainText(information.schedule)
        self.rules_edit.setPlainText(information.rules)
        self.server_info_edit.setPlainText(information.server_info)
        self.other_expose_checks["schedule"].setChecked(information.expose_schedule)
        self.other_expose_checks["rules"].setChecked(information.expose_rules)
        self.other_expose_checks["server_info"].setChecked(
            information.expose_server_info
        )
        self._loading = False
        self.save_button.setEnabled(False)
        self.status_label.clear()
        self._refresh_preview()

    def _values_changed(self, *_args) -> None:
        if self._loading:
            return
        self.save_button.setEnabled(True)
        self.status_label.setText("Unsaved changes")
        self.store.update_live(self._live_candidate())
        self._refresh_preview()

    def _live_candidate(self) -> ChannelInformation:
        links: dict[str, SocialLink] = {}
        for service_id, (include, edit, expose, _error) in self.social_rows.items():
            try:
                url = normalize_social_url(edit.text())
            except ValueError:
                url = ""
            links[service_id] = SocialLink(
                enabled_in_socials=include.isChecked(),
                url=url,
                expose_as_variable=expose.isChecked(),
            )
        return ChannelInformation(
            social_links=links,
            schedule=normalize_multiline_text(self.schedule_edit.toPlainText()),
            expose_schedule=self.other_expose_checks["schedule"].isChecked(),
            rules=normalize_multiline_text(self.rules_edit.toPlainText()),
            expose_rules=self.other_expose_checks["rules"].isChecked(),
            server_info=normalize_multiline_text(self.server_info_edit.toPlainText()),
            expose_server_info=self.other_expose_checks["server_info"].isChecked(),
        )

    def _candidate(self, *, show_errors: bool) -> ChannelInformation | None:
        links: dict[str, SocialLink] = {}
        valid = True
        for service_id, (include, edit, expose, error) in self.social_rows.items():
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
            links[service_id] = SocialLink(
                enabled_in_socials=include.isChecked(),
                url=url,
                expose_as_variable=expose.isChecked(),
            )
        if not valid:
            return None
        return ChannelInformation(
            social_links=links,
            schedule=normalize_multiline_text(self.schedule_edit.toPlainText()),
            expose_schedule=self.other_expose_checks["schedule"].isChecked(),
            rules=normalize_multiline_text(self.rules_edit.toPlainText()),
            expose_rules=self.other_expose_checks["rules"].isChecked(),
            server_info=normalize_multiline_text(self.server_info_edit.toPlainText()),
            expose_server_info=self.other_expose_checks["server_info"].isChecked(),
        )

    def _refresh_preview(self) -> None:
        parts: list[str] = []
        seen: set[str] = set()
        for service_id, label in SOCIAL_SERVICES:
            include, edit, _expose, _error = self.social_rows[service_id]
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
