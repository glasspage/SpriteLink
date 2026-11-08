"""Selection dialog for SpriteLink .sldata import/export."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFrame, QLabel, QLineEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

from spritelink_data_transfer import ARCHIVE_CATEGORIES


class _ChatroomsCheckBox(QCheckBox):
    """Clicking a partial category selects all rooms, not the next Qt tristate."""

    def __init__(self) -> None:
        super().__init__("Chatrooms")
        self.setTristate(True)

    def nextCheckState(self) -> None:
        state = (
            Qt.CheckState.Unchecked
            if self.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        self.setCheckState(state)


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
        layout.addWidget(QLabel(f"Select data to {mode.lower()}:"))

        self.category_checks: dict[str, QCheckBox] = {}
        self.room_checks: dict[str, QCheckBox] = {}
        self.rooms_area: QScrollArea | None = None
        self._syncing_rooms = False

        for category in ARCHIVE_CATEGORIES:
            check = (
                _ChatroomsCheckBox() if category == "Chatrooms"
                else QCheckBox(category)
            )
            check.setEnabled(category in available)
            check.setChecked(category in available)
            self.category_checks[category] = check
            layout.addWidget(check)

            if category == "Chatrooms" and rooms is not None:
                # Room selection is part of the main Chatrooms checkbox
                # for both importing and exporting.
                self.rooms_area = QScrollArea()
                self.rooms_area.setWidgetResizable(True)
                self.rooms_area.setMaximumHeight(190)
                self.rooms_area.setFrameShape(QFrame.Shape.NoFrame)
                room_panel = QWidget()
                room_layout = QVBoxLayout(room_panel)
                room_layout.setContentsMargins(20, 0, 0, 4)
                room_layout.setSpacing(2)
                for room in rooms:
                    room_check = QCheckBox(room["nickname"])
                    room_check.setChecked(True)
                    room_check.setToolTip(room["nickname"])
                    room_layout.addWidget(room_check)
                    self.room_checks[room["id"]] = room_check
                    room_check.toggled.connect(self._sync_chatroom_category)
                room_layout.addStretch()
                self.rooms_area.setWidget(room_panel)
                layout.addWidget(self.rooms_area)
                check.stateChanged.connect(self._set_all_room_checks)
                self._update_room_selection()

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
            "Optional, recommended" if mode == "export" else "Password"
        )
        if mode == "export":
            layout.addWidget(QLabel("Password"))
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
            check.stateChanged.connect(self._update_save_enabled)
        self.password_entry.textChanged.connect(self._update_save_enabled)
        self._update_save_enabled()

    def _set_all_room_checks(self, state: int) -> None:
        if self._syncing_rooms:
            return
        self._syncing_rooms = True
        try:
            checked = state != Qt.CheckState.Unchecked.value
            for room_check in self.room_checks.values():
                room_check.setChecked(checked)
        finally:
            self._syncing_rooms = False
        self._update_room_selection()

    def _sync_chatroom_category(self, _checked: bool = False) -> None:
        if self._syncing_rooms:
            return
        selected_count = sum(check.isChecked() for check in self.room_checks.values())
        total = len(self.room_checks)
        state = (
            Qt.CheckState.Checked if selected_count == total
            else Qt.CheckState.PartiallyChecked if selected_count
            else Qt.CheckState.Unchecked
        )
        self._syncing_rooms = True
        try:
            self.category_checks["Chatrooms"].setCheckState(state)
        finally:
            self._syncing_rooms = False
        self._update_room_selection()

    def _update_room_selection(self) -> None:
        if self.rooms_area is None:
            return
        chatrooms = self.category_checks["Chatrooms"]
        self.rooms_area.setVisible(chatrooms.isEnabled())
        self.rooms_area.setEnabled(chatrooms.isChecked())
        self._update_save_enabled()

    def selected_categories(self) -> tuple[str, ...]:
        return tuple(
            category for category, check in self.category_checks.items()
            if check.isEnabled() and check.isChecked()
        )

    def selected_room_ids(self) -> set[str] | None:
        # None means all rooms; this also preserves the existing export API.
        if not self.room_checks or all(
            check.isChecked() for check in self.room_checks.values()
        ):
            return None
        return {
            room_id for room_id, check in self.room_checks.items()
            if check.isChecked()
        }

    def password(self) -> str:
        return self.password_entry.text()

    def _update_save_enabled(self, _changed: Any = None) -> None:
        # Called during construction before the button box exists.
        if not hasattr(self, "action_button"):
            return
        selected = self.selected_categories()
        valid = bool(selected)
        if self.password_required:
            valid = valid and bool(self.password())
        if "Chatrooms" in selected and self.room_checks:
            valid = valid and bool(
                self.selected_room_ids() is None or self.selected_room_ids()
            )
        self.action_button.setEnabled(valid)
