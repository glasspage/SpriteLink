"""Selection dialog for SpriteLink .sldata import/export."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QLabel, QLineEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

from spritelink_data_transfer import ARCHIVE_CATEGORIES


class DataTransferDialog(QDialog):
    def __init__(
        self, mode: str, available: tuple[str, ...],
        rooms: list[dict[str, str]] | None = None,
        parent: QWidget | None = None,
        password_required: bool = True,
    ) -> None:
        super().__init__(parent)
        if mode not in ("export", "import"):
            raise ValueError("Unknown data transfer mode.")
        self.mode = mode
        action = "Export" if mode == "export" else "Import"
        self.setWindowTitle(f"{action} Data")
        self.setMinimumWidth(330)
        layout = QVBoxLayout(self)
        title = QLabel(f"Select data to {mode.lower()}:")
        layout.addWidget(title)

        self.category_checks: dict[str, QCheckBox] = {}
        self.specific_rooms: QCheckBox | None = None
        self.room_checks: dict[str, QCheckBox] = {}
        self.rooms_area: QScrollArea | None = None

        for category in ARCHIVE_CATEGORIES:
            check = QCheckBox(category)
            check.setEnabled(category in available)
            check.setChecked(category in available)
            self.category_checks[category] = check
            layout.addWidget(check)

            if category == "Chatrooms" and mode == "export":
                # Room selection belongs directly beneath its Chatrooms
                # checkbox, not below the unrelated categories.
                room_options = QWidget()
                options_layout = QVBoxLayout(room_options)
                options_layout.setContentsMargins(20, 0, 0, 4)
                options_layout.setSpacing(4)
                self.specific_rooms = QCheckBox("Specific chatrooms only")
                options_layout.addWidget(self.specific_rooms)
                self.rooms_area = QScrollArea()
                self.rooms_area.setWidgetResizable(True)
                self.rooms_area.setMaximumHeight(190)
                self.rooms_area.setFrameShape(QFrame.Shape.NoFrame)
                room_panel = QWidget()
                room_layout = QVBoxLayout(room_panel)
                room_layout.setContentsMargins(20, 0, 0, 0)
                room_layout.setSpacing(2)
                for room in rooms or []:
                    room_check = QCheckBox(room["nickname"])
                    room_check.setChecked(True)
                    room_check.setToolTip(room["nickname"])
                    room_layout.addWidget(room_check)
                    self.room_checks[room["id"]] = room_check
                    room_check.toggled.connect(self._update_save_enabled)
                room_layout.addStretch()
                self.rooms_area.setWidget(room_panel)
                options_layout.addWidget(self.rooms_area)
                layout.addWidget(room_options)
                self.specific_rooms.toggled.connect(self._update_room_selection)
                check.toggled.connect(self._update_room_selection)
                self._update_room_selection()

        if mode == "import" and any(
            name not in available for name in ARCHIVE_CATEGORIES
        ):
            unavailable = QLabel(
                "Unavailable categories are not included in this file."
            )
            unavailable.setWordWrap(True)
            layout.addWidget(unavailable)

        if mode == "import":
            self.identity_warning = QLabel(
                "Importing Identity replaces your signing key and changes "
                "your user ID. Export a backup first."
            )
            self.identity_warning.setWordWrap(True)
            layout.addWidget(self.identity_warning)
            self.category_checks["Identity"].toggled.connect(
                self.identity_warning.setVisible
            )
            self.identity_warning.setVisible(
                self.category_checks["Identity"].isChecked()
            )
        self.password_entry = QLineEdit()
        self.password_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_entry.setPlaceholderText(
            "Recommended" if mode == "export" else "Password"
        )
        if mode == "export":
            layout.addWidget(QLabel("Password (optional)"))
            self.password_entry.setToolTip(
                "Without a password, the export is not encrypted and "
                "can reveal chatroom keys and identity data."
            )
            layout.addWidget(self.password_entry)
        elif password_required:
            layout.addWidget(QLabel("Password"))
            layout.addWidget(self.password_entry)
        # Password-free .sldata files require no password prompt.
        self.password_required = mode == "import" and password_required

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.action_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        )
        self.action_button.setText(action)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for check in self.category_checks.values():
            check.toggled.connect(self._update_save_enabled)
        self.password_entry.textChanged.connect(self._update_save_enabled)
        self._update_save_enabled()

    def _update_room_selection(self, _checked: bool = False) -> None:
        if self.specific_rooms is None or self.rooms_area is None:
            return
        enabled = self.category_checks["Chatrooms"].isChecked()
        self.specific_rooms.setEnabled(enabled)
        self.rooms_area.setVisible(enabled and self.specific_rooms.isChecked())
        self._update_save_enabled()

    def selected_categories(self) -> tuple[str, ...]:
        return tuple(
            category for category, check in self.category_checks.items()
            if check.isEnabled() and check.isChecked()
        )

    def selected_room_ids(self) -> set[str] | None:
        if self.specific_rooms is None or not self.specific_rooms.isChecked():
            return None
        return {
            room_id for room_id, check in self.room_checks.items()
            if check.isChecked()
        }

    def password(self) -> str:
        return self.password_entry.text()

    def _update_save_enabled(self, _checked: Any = None) -> None:
        # Called during construction of the category widgets, before the
        # button box exists.
        if not hasattr(self, "action_button"):
            return
        selected = self.selected_categories()
        valid = bool(selected)
        if self.password_required:
            valid = valid and bool(self.password())
        if self.mode == "export":
            if (
                "Chatrooms" in selected
                and self.specific_rooms is not None
                and self.specific_rooms.isChecked()
            ):
                valid = valid and bool(self.selected_room_ids())
        self.action_button.setEnabled(valid)
