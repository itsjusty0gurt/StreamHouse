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
            str, tuple[QCheckBox, QLineEdit, QPushButton, QLabel]
        ] = {}
        self._social_labels: dict[str, QLabel] = {}
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
        social_help = QLabel("Update each row to save its link and command setup. Include controls !socials only.")
        social_help.setWordWrap(True)
        social_layout.addWidget(social_help)
        self.social_grid = QGridLayout()
        self.social_headers = (
            QLabel("Include in !socials"),
            QLabel("Service"),
            QLabel("Link"),
            QLabel(""),
        )
        for row, (service_id, label) in enumerate(SOCIAL_SERVICES, start=1):
            include = QCheckBox()
            include.setObjectName(f"channelInformationInclude{service_id.title()}")
            edit = QLineEdit()
            edit.setObjectName(f"channelInformation{service_id.title()}Url")
            edit.setPlaceholderText("https://")
            update = QPushButton("Update")
            update.setObjectName(f"channelInformationUpdate{service_id.title()}")
            error = QLabel()
            error.setObjectName(f"channelInformation{service_id.title()}Error")
            error.setStyleSheet("color: #e5a84b;")
            error.setWordWrap(True)
            error.hide()
            service_label = QLabel(label)
            service_label.setWordWrap(True)
            self._social_labels[service_id] = service_label
            self.social_rows[service_id] = include, edit, update, error
            include.toggled.connect(lambda _value, key=service_id: self._social_changed(key))
            edit.textChanged.connect(lambda _value, key=service_id: self._social_changed(key))
            update.clicked.connect(lambda _checked=False, key=service_id: self.update_social(key))
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
            self._other_headings[field_id] = heading
            editor.textChanged.connect(self._values_changed)
        layout.addWidget(other_group)

        self.enable_after_saving_check = QCheckBox()
        self.enable_after_saving_check.setObjectName(
            "channelInformationEnableAfterSaving"
        )
        self.enable_after_saving_check.hide()
        layout.addWidget(self.enable_after_saving_check)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Save Other Information")
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
        for column in range(5):
            self.social_grid.setColumnStretch(column, 0)

        if compact:
            for header in self.social_headers:
                header.hide()
            for row, (service_id, _label) in enumerate(SOCIAL_SERVICES):
                include, edit, update, error = self.social_rows[service_id]
                include.setText("Include in !socials")
                base_row = row * 4
                self.social_grid.addWidget(
                    self._social_labels[service_id], base_row, 0, 1, 2
                )
                self.social_grid.addWidget(edit, base_row + 1, 0)
                self.social_grid.addWidget(update, base_row + 1, 1)
                self.social_grid.addWidget(include, base_row + 2, 0, 1, 2)
                self.social_grid.addWidget(error, base_row + 3, 0, 1, 2)
            self.social_grid.setColumnStretch(0, 1)
            self.social_grid.setColumnStretch(1, 0)

            for row, (field_id, editor) in enumerate(
                (
                    ("schedule", self.schedule_edit),
                    ("rules", self.rules_edit),
                    ("server_info", self.server_info_edit),
                )
            ):
                base_row = row * 2
                self.other_layout.addWidget(
                    self._other_headings[field_id], base_row, 0, 1, 2
                )
                self.other_layout.addWidget(editor, base_row + 1, 0, 1, 2)
            self.other_layout.setColumnStretch(0, 1)
            self.other_layout.setColumnStretch(1, 0)
            self.content_widget.layout().activate()
            self.content_widget.adjustSize()
            return

        for column, header in enumerate(self.social_headers):
            header.show()
            self.social_grid.addWidget(header, 0, column)
        for row, (service_id, _label) in enumerate(SOCIAL_SERVICES, start=1):
            include, edit, update, error = self.social_rows[service_id]
            include.setText("")
            include.setToolTip("Include in !socials")
            base_row = row * 2 - 1
            self.social_grid.addWidget(include, base_row, 0)
            self.social_grid.addWidget(self._social_labels[service_id], base_row, 1)
            self.social_grid.addWidget(edit, base_row, 2)
            self.social_grid.addWidget(update, base_row, 3)
            self.social_grid.addWidget(error, base_row + 1, 1, 1, 3)
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
        for service_id, (include, edit, update, error) in self.social_rows.items():
            link = information.social_links[service_id]
            include.setChecked(link.enabled_in_socials)
            edit.setText(link.url)
            update.setEnabled(False)
            error.clear()
        self.schedule_edit.setPlainText(information.schedule)
        self.rules_edit.setPlainText(information.rules)
        self.server_info_edit.setPlainText(information.server_info)
        self._loading = False
        self.save_button.setEnabled(False)
        self.status_label.clear()
        self._refresh_preview()

    def _social_changed(self, service_id: str) -> None:
        if self._loading:
            return
        include, edit, update, error = self.social_rows[service_id]
        committed = self.store.snapshot().social_links[service_id]
        update.setEnabled(
            edit.text() != committed.url
            or include.isChecked() != committed.enabled_in_socials
        )
        error.clear()
        error.hide()

    def update_social(self, service_id: str) -> None:
        include, edit, update, error = self.social_rows[service_id]
        try:
            url = normalize_social_url(edit.text())
            if self.command_store is not None:
                self.command_store.commit_social(
                    self.store, service_id, url, include.isChecked()
                )
            else:
                candidate = self.store.snapshot()
                candidate.social_links[service_id] = SocialLink(include.isChecked(), url)
                self.store.save(candidate)
        except (OSError, ValueError) as failure:
            error.setText(f"Could not update: {failure}")
            error.show()
            update.setEnabled(True)
            return
        # Only refresh this row: other social and multiline drafts must survive.
        edit.setText(self.store.snapshot().social_links[service_id].url)
        update.setEnabled(False)
        error.clear()
        error.hide()
        self._refresh_preview()
        self.status_label.setText("Social link updated.")
        self.saved.emit()

    def _values_changed(self, *_args) -> None:
        if self._loading:
            return
        committed = self.store.snapshot()
        self.save_button.setEnabled(any(
            editor.toPlainText() != getattr(committed, field_id)
            for field_id, editor in self._other_editors()
        ))
        self.status_label.setText("Unsaved other information" if self.save_button.isEnabled() else "")

    def _other_editors(self):
        return (
            ("schedule", self.schedule_edit),
            ("rules", self.rules_edit),
            ("server_info", self.server_info_edit),
        )

    def _refresh_preview(self) -> None:
        self.socials_preview_label.setText(
            self.store.build_social_links_message()
            or "Setup Required — update at least one included social link."
        )

    def save_values(self) -> None:
        candidate = self.store.snapshot()
        for field_id, editor in self._other_editors():
            setattr(candidate, field_id, normalize_multiline_text(editor.toPlainText()))
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
            requirement = self.command_store.setup_requirement(self._enable_default_id)
            if self.store.field_available(requirement):
                try:
                    command = self.command_store.configure_default(self._enable_default_id)
                    self.command_store.set_enabled(command.trigger_id, True)
                    enabled = True
                except (OSError, ValueError) as error:
                    self.status_label.setText(f"Information saved; could not enable command: {error}")
                    self.saved.emit()
                    return
        self._loading = True
        for field_id, editor in self._other_editors():
            editor.setPlainText(getattr(candidate, field_id))
        self._loading = False
        self.save_button.setEnabled(False)
        self.status_label.setText(
            "Other information saved and command enabled." if enabled else "Other information saved."
        )
        self.saved.emit()

    def focus_for_command(self, default_id: str) -> None:
        requirement = TwitchCommandTriggerStore.setup_requirement(default_id)
        social_requirement = requirement in {"discord_url", "youtube_url", "socials"}
        self._enable_default_id = default_id if requirement and not social_requirement else ""
        command = self.command_store.default(default_id) if self.command_store else None
        self.enable_after_saving_check.setVisible(
            bool(self._enable_default_id and (command is None or not command.enabled))
        )
        self.enable_after_saving_check.setChecked(False)
        self.enable_after_saving_check.setText(
            f"Configure and enable !{default_id} after saving"
            if command is None
            else f"Enable !{command.name} after saving"
        )
        if requirement.removesuffix("_url") in self.social_rows:
            self.social_rows[requirement.removesuffix("_url")][1].setFocus()
        elif requirement == "socials":
            self.social_rows["discord"][1].setFocus()
        elif requirement == "schedule":
            self.schedule_edit.setFocus()
        elif requirement == "rules":
            self.rules_edit.setFocus()
        elif requirement == "server_info":
            self.server_info_edit.setFocus()
