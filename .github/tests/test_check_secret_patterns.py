import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_secret_patterns as checker  # noqa: E402


class SecretPatternTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, name: str, content: str) -> None:
        (self.root / name).write_text(content + "\n", encoding="utf-8")

    def test_finds_values_in_env_source_and_configuration_files(self) -> None:
        value = "accidentally-real-value"
        key = "API" + "_KEY"
        password = "PASS" + "WORD"
        self.write(".env.example", f"{key}={value}")
        self.write("config.ts", f"const TOKEN = {value};")
        self.write("Config.java", f'String {key} = "{value}";')
        self.write("application.properties", f"{key}={value}")
        self.write(".env.secrets", f"DB_{password}={value}")
        self.assertEqual(
            set(checker.find_secret_files(self.root)),
            {".env.example", "config.ts", "Config.java", "application.properties", ".env.secrets"},
        )

    def test_finds_openai_style_key(self) -> None:
        self.write("client.py", 'value = "sk-' + "a1B2c3D4e5F6g7H8i9J0k1" + '"')
        self.assertEqual(checker.find_secret_files(self.root), ["client.py"])

    def test_ignores_explicit_placeholders(self) -> None:
        self.write(".env.example", "DB_" + "PASSWORD=" + "replace" + "-me")
        self.assertEqual(checker.find_secret_files(self.root), [])

    def test_main_fails_without_printing_the_value(self) -> None:
        value = "accidentally-real-value"
        self.write("config.py", "API" + f"_KEY = '{value}'")
        current = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, current)
        with tempfile.TemporaryFile("w+", encoding="utf-8") as stderr:
            original, sys.stderr = sys.stderr, stderr
            try:
                self.assertEqual(checker.main(), 1)
            finally:
                sys.stderr = original
            stderr.seek(0)
            output = stderr.read()
        self.assertIn("config.py", output)
        self.assertNotIn(value, output)


if __name__ == "__main__":
    unittest.main()
