from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'integracao/painel/manutencao.py'
spec = importlib.util.spec_from_file_location('manutencao', MODULE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'painel'
        self.base.mkdir()
        self.db = self.base / m.DATABASES[0]
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('CREATE TABLE dados(valor TEXT)')
            con.execute("INSERT INTO dados VALUES ('preservar')")

    def test_backup_restores_committed_wal_and_detects_corruption(self):
        with closing(sqlite3.connect(self.db)) as con, con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute("INSERT INTO dados VALUES ('wal')")
            con.commit()
            archive = m.create_backup(self.base, self.root / 'externo')
            self.assertEqual(m.verify_backup(archive), [self.db.name])
            with zipfile.ZipFile(archive) as z:
                restored = self.root / 'restored.sqlite3'
                restored.write_bytes(z.read(self.db.name))
                manifest = z.read('manifest.json')
            with closing(sqlite3.connect(restored)) as copy:
                self.assertEqual(copy.execute('SELECT valor FROM dados').fetchall(), [('preservar',), ('wal',)])
        broken = self.root / 'broken.zip'
        with zipfile.ZipFile(broken, 'w') as z:
            z.writestr('manifest.json', manifest)
            z.writestr(self.db.name, b'corrupted')
        with self.assertRaises(ValueError):
            m.verify_backup(broken)

    def test_update_preserves_data_credentials_and_old_code(self):
        source = self.root / 'release'
        source.mkdir()
        for name in ('index.html', 'iniciar_interface.py', 'manutencao.py', 'whatsapp_meta.py'):
            (source / name).write_text('# new')
        (source / '.env').write_text('do not copy')
        (source / self.db.name).write_bytes(b'do not copy')
        (self.base / '.env').write_text('local config')
        (self.base / 'sonifarma_tls.key').write_text('local key')
        (self.base / 'index.html').write_text('old')
        archive, rollback = m.update_code(self.base, source, self.root / 'backups')
        self.assertEqual((self.base / 'index.html').read_text(), '# new')
        self.assertEqual((rollback / 'index.html').read_text(), 'old')
        self.assertEqual((self.base / '.env').read_text(), 'local config')
        self.assertEqual((self.base / 'sonifarma_tls.key').read_text(), 'local key')
        with closing(sqlite3.connect(self.db)) as con, con:
            self.assertEqual(con.execute('SELECT valor FROM dados').fetchone()[0], 'preservar')
        m.verify_backup(archive)

    def test_incomplete_update_and_empty_backup_are_rejected(self):
        source = self.root / 'empty'
        source.mkdir()
        with self.assertRaises(ValueError):
            m.update_code(self.base, source, self.root / 'backup')
        with self.assertRaises(ValueError):
            m.create_backup(source, self.root / 'backup')

    def test_backup_closes_all_connections_without_garbage_collection(self):
        connections = []
        original_connect = sqlite3.connect

        class TrackedConnection(sqlite3.Connection):
            explicitly_closed = False

            def close(self):
                super().close()
                self.explicitly_closed = True

        def connect(*args, **kwargs):
            connection = original_connect(*args, factory=TrackedConnection, **kwargs)
            connections.append(connection)  # Keep references so GC cannot hide leaks.
            return connection

        with patch.object(m.sqlite3, 'connect', side_effect=connect):
            archive = m.create_backup(self.base, self.root / 'backup')
            m.verify_backup(archive)
        self.assertEqual(len(connections), 4)
        self.assertTrue(all(con.explicitly_closed for con in connections))
        self.assertEqual(sorted(p.name for p in (self.root / 'backup').iterdir()), [archive.name])

    def test_failed_backup_closes_connections_and_publishes_no_archive(self):
        connections = []
        original_connect = sqlite3.connect

        class FailingConnection(sqlite3.Connection):
            explicitly_closed = False

            def backup(self, target, **kwargs):
                raise sqlite3.OperationalError('Simulated backup failure')

            def close(self):
                super().close()
                self.explicitly_closed = True

        def connect(*args, **kwargs):
            connection = original_connect(*args, factory=FailingConnection, **kwargs)
            connections.append(connection)
            return connection

        destination = self.root / 'backup'
        with patch.object(m.sqlite3, 'connect', side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                m.create_backup(self.base, destination)
        self.assertEqual(len(connections), 2)
        self.assertTrue(all(con.explicitly_closed for con in connections))
        self.assertEqual(list(destination.iterdir()), [])

    def test_failed_copy_rolls_back_code(self):
        source = self.root / 'release'
        source.mkdir()
        for name in ('index.html', 'iniciar_interface.py', 'manutencao.py', 'whatsapp_meta.py'):
            (source / name).write_text('# new')
            (self.base / name).write_text('# old')
        original_copy = m.shutil.copyfile
        def failing_copy(src, dst, **kwargs):
            if Path(src) == source / 'index.html':
                raise OSError('Simulated write failure')
            return original_copy(src, dst, **kwargs)
        with patch.object(m.shutil, 'copyfile', side_effect=failing_copy):
            with self.assertRaises(OSError):
                m.update_code(self.base, source, self.root / 'backups')
        for name in ('index.html', 'iniciar_interface.py', 'manutencao.py', 'whatsapp_meta.py'):
            self.assertEqual((self.base / name).read_text(), '# old')


if __name__ == '__main__':
    unittest.main()
