"""Offline documentation checks. Does not open the business database or start services."""
import ast
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = Path(__file__).resolve().parent
errors = []

# Record only source/config/product documentation, never runtime data or profiles.
sources = sorted(set(
    list((ROOT / 'leadworkbench').rglob('*.py'))
    + list((ROOT / 'frontend/src').rglob('*.ts'))
    + list((ROOT / 'frontend/src').rglob('*.tsx'))
    + list((ROOT / 'frontend/src').rglob('*.css'))
    + list((ROOT / 'scripts').glob('*.sh'))
    + list((ROOT / 'scripts').glob('import_*_geography.py'))
    + list((ROOT / 'scripts').glob('patch_maps_stability.py'))
    + list((ROOT / 'scripts').glob('maps_stability.patch'))
    + list((ROOT / 'leadworkbench/datasets').glob('*.json'))
    + list((ROOT / 'docs/product').glob('*.md'))
    + [ROOT / 'pyproject.toml', ROOT / 'frontend/package.json', ROOT / 'frontend/vite.config.ts']
))
manifest = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
(DOCS / 'source-manifest.json').write_text(json.dumps({
    'documentation_date': '2026-09-26',
    'purpose': 'Current source fingerprint; no runtime data or secrets included',
    'sha256': manifest,
}, ensure_ascii=False, indent=2) + '\n')

schema = (DOCS / 'schema.current.sql').read_text()
for source, expected in re.findall(r'^-- SHA256 (.+): ([0-9a-f]{64})$', schema, re.M):
    if manifest.get(source) != expected:
        errors.append('DDL source fingerprint mismatch: ' + source)
with sqlite3.connect(':memory:') as connection:
    connection.executescript(schema)
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = list(connection.execute("SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
    integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_keys = connection.execute('PRAGMA foreign_key_check').fetchall()
    if integrity != 'ok' or foreign_keys:
        errors.append('DDL integrity or foreign key check failed')

spec = json.loads((DOCS / 'openapi.current.json').read_text())
methods = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head'}
openapi_routes = {(method, path) for path, item in spec['paths'].items() for method in item if method in methods}
code_routes = set()
for node in ast.walk(ast.parse((ROOT / 'leadworkbench/api.py').read_text())):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for decorator in node.decorator_list:
        if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in methods and decorator.args
                and isinstance(decorator.args[0], ast.Constant)):
            path = decorator.args[0].value.replace('{path:path}', '{path}')
            code_routes.add((decorator.func.attr, path))
if code_routes != openapi_routes:
    errors.append('OpenAPI route set differs from api.py decorators')

# Structural checks only; Mermaid parser/visual rendering is explicitly out of scope.
diagrams = []
links_checked = 0
for path in sorted(DOCS.glob('*.md')):
    text = path.read_text()
    if len(re.findall(r'^```', text, re.M)) % 2:
        errors.append('Unclosed code fence: ' + path.name)
    for ordinal, match in enumerate(re.finditer(r'```mermaid\n(.*?)\n```', text, re.S), 1):
        diagram = match.group(1)
        kind = diagram.splitlines()[0].strip()
        diagrams.append({'file': path.name, 'number': ordinal, 'kind': kind,
                         'line': text[:match.start()].count('\n') + 1})
        if kind not in {'flowchart LR', 'flowchart TB', 'sequenceDiagram', 'erDiagram', 'stateDiagram-v2'}:
            errors.append('Unrecognized diagram header: ' + path.name + ':' + kind)
        if path.name == 'database.md' and kind == 'erDiagram':
            for line in diagram.splitlines()[1:]:
                if ':' in line:
                    pieces = line.split(':', 1)[0].split()
                    for entity in (pieces[0], pieces[-1]):
                        if entity not in tables:
                            errors.append('ER references missing actual table: ' + entity)
    for destination in re.findall(r'\]\(([^)]+)\)', text):
        destination = destination.strip('<>').split('#', 1)[0]
        if not destination or re.match(r'^[a-z]+://', destination):
            continue
        # This suite uses simple local paths without spaces or optional titles.
        if not (path.parent / destination).resolve().exists():
            errors.append('Broken local link: ' + path.name + ' -> ' + destination)
        links_checked += 1
    for test_path in re.findall(r'`((?:frontend/)?tests/[^`]+\.(?:py|ts))`', text):
        if not (ROOT / test_path).is_file():
            errors.append('Missing referenced test: ' + test_path)

result = {
    'documentation_date': '2026-09-26',
    'scope': 'Offline source, links, OpenAPI route set and in-memory DDL; no live DB, network or service changes',
    'source_files': len(manifest), 'local_links_checked': links_checked,
    'api_business_operations': sum(path.startswith('/api/') for _, path in code_routes),
    'openapi_operations_including_spa': len(openapi_routes),
    'schema_tables': len(tables), 'explicit_indexes': len(indexes),
    'schema_integrity': integrity, 'foreign_key_errors': foreign_keys,
    'diagram_count': len(diagrams), 'diagrams': diagrams,
    'mermaid_check': 'Code fences, diagram headers and current ER table references only; not rendered',
    'errors': errors, 'passed': not errors,
}
(DOCS / 'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({key: value for key, value in result.items() if key != 'diagrams'}, ensure_ascii=False, indent=2))
raise SystemExit(bool(errors))
