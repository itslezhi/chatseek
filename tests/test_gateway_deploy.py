import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from beta_gateway.deploy.backup import backup


class GatewayDeploymentTests(unittest.TestCase):
    def test_backup_includes_committed_wal_and_survives_original_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'usage.sqlite3'
            with sqlite3.connect(source) as live:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute('CREATE TABLE quota (used INTEGER)')
                live.execute('INSERT INTO quota VALUES (12)')
                live.commit()
                # Keep the writer open so committed data can still be in WAL.
                copy = backup(source, root / 'backups')
            source.unlink()
            with sqlite3.connect(copy) as restored:
                self.assertEqual(restored.execute('SELECT used FROM quota').fetchone()[0], 12)
            self.assertEqual(os.stat(copy).st_mode & 0o777, 0o600)

    def test_retention_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'usage.sqlite3'
            with sqlite3.connect(source) as live:
                live.execute('CREATE TABLE quota (used INTEGER)')
            directory = root / 'backups'
            directory.mkdir()
            unrelated = directory / 'keep.txt'
            unrelated.write_text('retain this')
            copies = [backup(source, directory, keep=2) for _ in range(3)]
            self.assertFalse(copies[0].exists())
            self.assertTrue(copies[-1].exists())
            self.assertEqual(len(list(directory.glob('usage-*.sqlite3'))), 2)
            self.assertTrue(unrelated.exists())

    def test_missing_database_is_not_silently_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):
                backup(source, Path(temporary) / 'backups')
            self.assertFalse(source.exists())

    def test_deployment_archive_contains_configs_and_no_local_data(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('gateway_builder', root / 'scripts/build_beta_gateway.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            archive = builder.build(Path(temporary) / 'gateway.zip')
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                self.assertIn('chatseek-beta-gateway/deploy/install.sh', names)
                self.assertIn('chatseek-beta-gateway/server.py', names)
                self.assertIn('chatseek-beta-gateway/DEPLOY.md', names)
                self.assertFalse(any(Path(name).name.startswith('.') for name in names))
                self.assertFalse(any(name.endswith(('.env', '.sqlite3', '.db', '.pyc')) for name in names))
                self.assertEqual(package.testzip(), None)


if __name__ == '__main__':
    unittest.main()
