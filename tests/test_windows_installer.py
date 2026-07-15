from pathlib import Path
import unittest


INSTALLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "packaging"
    / "windows"
    / "SpriteLink.iss"
)


class WindowsInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = INSTALLER_PATH.read_text(encoding="utf-8")

    def test_start_menu_shortcut_is_a_default_enabled_task(self) -> None:
        task_line = next(
            line
            for line in self.installer.splitlines()
            if line.startswith('Name: "startmenuicon"')
        )
        self.assertIn("Create a &Start menu shortcut", task_line)
        self.assertNotIn("Flags: unchecked", task_line)
        self.assertIn("UsePreviousTasks=yes", self.installer)

    def test_start_menu_icon_is_linked_to_task(self) -> None:
        icon_line = next(
            line
            for line in self.installer.splitlines()
            if line.startswith('Name: "{autoprograms}')
        )
        self.assertIn("Tasks: startmenuicon", icon_line)
        self.assertIn('Filename: "{app}\\{#AppExeName}"', icon_line)


if __name__ == "__main__":
    unittest.main()
