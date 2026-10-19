"""Backup verificável e atualização somente de código, sem dependências extras."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
import ssl
import urllib.request

DATABASES = ('agenda_posvenda.sqlite3', 'posologias_confirmadas.sqlite3',
             'acesso_auditoria.sqlite3', 'nomes_comerciais.sqlite3')
# A atualização nunca copia configurações, credenciais, certificados ou bancos.
CODE_FILES = ('iniciar_interface.py', 'index.html', 'manutencao.py',
              'SONIFARMA_CONTROLE.ps1', 'SONIFARMA_AUTOMATICO.ps1',
              'START_SONIFARMA.bat', 'STOP_SONIFARMA.bat',
              'RESTART_SONIFARMA.bat', 'STATUS_SONIFARMA.bat')


def verify_backup(archive):
    with tempfile.TemporaryDirectory() as work, zipfile.ZipFile(archive) as z:
        manifest = json.loads(z.read('manifest.json'))
        files = manifest['files']
        if not files or not set(files).issubset(DATABASES):
            raise ValueError('Backup sem bancos reconhecidos')
        if len(z.namelist()) != len(set(z.namelist())) or set(z.namelist()) != set(files) | {'manifest.json'}:
            raise ValueError('Conteúdo inesperado no backup')
        for name, digest in files.items():
            data = z.read(name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Checksum inválido: ' + name)
            path = Path(work) / name
            path.write_bytes(data)
            with closing(sqlite3.connect(path)) as con:
                if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('Banco inválido: ' + name)
    return sorted(files)


def create_backup(base, destination):
    base, destination = Path(base), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    target = destination / ('sonifarma_backup_' + stamp + '.zip')
    with tempfile.TemporaryDirectory(dir=destination) as work:
        work = Path(work)
        files = {}
        for name in DATABASES:
            source = base / name
            if not source.exists():
                continue
            # API de backup inclui transações confirmadas no WAL, mesmo com painel ativo.
            with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as src:
                with closing(sqlite3.connect(work / name)) as dst:
                    src.backup(dst)
            files[name] = hashlib.sha256((work / name).read_bytes()).hexdigest()
        if not files:
            raise ValueError('Nenhum banco local encontrado; backup não criado')
        temp_zip = work / 'backup.zip'
        with zipfile.ZipFile(temp_zip, 'w', zipfile.ZIP_DEFLATED) as z:
            for name in files:
                z.write(work / name, name)
            z.writestr('manifest.json', json.dumps({'version': 1, 'files': files}))
        verify_backup(temp_zip)
        temp_zip.replace(target)
    return target


def update_code(base, source, backup_dir):
    base, source = Path(base).resolve(), Path(source).resolve()
    if base == source:
        raise ValueError('Origem e instalação devem ser diferentes')
    # O controle Windows interrompe o painel antes desta operação.
    for required in ('index.html', 'iniciar_interface.py', 'manutencao.py'):
        if not (source / required).is_file():
            raise ValueError('Pacote incompleto: ' + required)
    import ast
    for name in CODE_FILES:
        if (source / name).is_file() and name.endswith('.py'):
            ast.parse((source / name).read_text(encoding='utf-8-sig'))
    backup = create_backup(base, backup_dir)
    rollback = Path(backup_dir) / ('codigo_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    rollback.mkdir()
    selected = [name for name in CODE_FILES if (source / name).is_file()]
    existing = []
    for name in selected:
        if (base / name).exists():
            shutil.copy2(base / name, rollback / name)
            existing.append(name)
    try:
        for name in selected:
            # Arquivo temporário no mesmo volume para substituição atômica por arquivo.
            with tempfile.NamedTemporaryFile(dir=base, delete=False) as f:
                pending = Path(f.name)
            try:
                shutil.copyfile(source / name, pending)
                pending.replace(base / name)
            finally:
                pending.unlink(missing_ok=True)
    except Exception:
        for name in selected:
            if name in existing:
                shutil.copy2(rollback / name, base / name)
            else:
                (base / name).unlink(missing_ok=True)
        raise
    return backup, rollback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('backup', 'verify', 'update', 'health'))
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--certificate', type=Path)
    args = parser.parse_args()
    if args.action == 'health':
        if not args.url:
            parser.error('health exige --url')
        context = ssl.create_default_context(cafile=str(args.certificate)) if args.certificate else None
        with urllib.request.urlopen(args.url.rstrip('/') + '/api/auth/status', context=context, timeout=5) as response:
            status = json.load(response)
            if response.status != 200 or not all(isinstance(status.get(k), bool) for k in ('inicializado', 'autenticado')):
                raise ValueError('Resposta de saude inesperada')
        print('Painel respondeu corretamente.')
    elif args.action == 'backup':
        print(create_backup(args.base, args.destination or args.base / 'backups'))
    elif args.action == 'verify':
        if not args.archive:
            parser.error('verify exige --archive')
        print('Restauração temporária e integridade verificadas:', ', '.join(verify_backup(args.archive)))
    else:
        if not args.source or not args.destination:
            parser.error('update exige --source e --destination; pare o painel antes')
        print(update_code(args.base, args.source, args.destination))


if __name__ == '__main__':
    main()
