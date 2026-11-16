#!/usr/bin/env python3

import argparse
import ast
import json
import os
import re
import shlex
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

MODULE_TO_PACKAGE = {
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "osgeo": "gdal",
}

IGNORED_PACKAGES = {"pip", "setuptools", "wheel"}
SKIPPED_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__"}
PIP_INSTALL = re.compile(r"\bpip[0-9.]*\"?\s+install\b(.*)")
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(.*)$")
CONSTRAINT = re.compile(r"^(==|~=|!=|<=|>=|<|>)\s*([A-Za-z0-9.*+!_-]+)$")
PYTHON_RUN = re.compile(r"\bpython[0-9.]*\s+(?:-[A-Za-z]+\s+)*([\w./\"' -]+?\.py)\b")
LIMITING = {"==", "~=", "<", "<="}


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def iter_files(root, patterns):
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            if not SKIPPED_DIRS.intersection(path.relative_to(root).parts):
                yield path


def parse_requirement(text):
    text = text.split(";")[0].split("#")[0].strip()
    match = REQUIREMENT.match(text)
    if not match:
        return None
    name, _, spec = match.groups()
    constraints = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        found = CONSTRAINT.match(part)
        if not found:
            return None
        constraints.append(found.groups())
    return normalize(name), constraints


def parse_pip_arguments(arguments):
    try:
        tokens = shlex.split(arguments, comments=True)
    except ValueError:
        tokens = arguments.split()
    requirements, files = [], []
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token in ("&&", "||", ";", "|", ">", ">>", "2>&1"):
            break
        if token in ("-r", "--requirement"):
            if index + 1 < len(tokens):
                files.append(tokens[index + 1])
            skip_next = True
            continue
        if token in ("-c", "--constraint", "-i", "--index-url", "--extra-index-url", "--target", "-t"):
            skip_next = True
            continue
        if token.startswith("-") or "/" in token or token.endswith((".txt", ".whl")):
            continue
        parsed = parse_requirement(token)
        if parsed and parsed[0] not in IGNORED_PACKAGES:
            requirements.append(parsed)
    return requirements, files


def scan_install_commands(root):
    installs = []
    for path in iter_files(root, ["*.yml", "*.yaml", "*.sh", "*.bash", "Dockerfile*"]):
        lines = path.read_text(errors="replace").splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            start = index
            while line.rstrip().endswith("\\") and index + 1 < len(lines):
                index += 1
                line = line.rstrip()[:-1] + " " + lines[index].strip()
            index += 1
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            match = PIP_INSTALL.search(stripped)
            if not match:
                continue
            requirements, files = parse_pip_arguments(match.group(1))
            location = f"{path.relative_to(root)}:{start + 1}"
            for name, constraints in requirements:
                installs.append({"package": name, "constraints": constraints, "where": location, "file": path})
            for file in files:
                installs.append({"requirements_file": file, "where": location, "file": path})
    return installs


def scan_requirement_files(root):
    entries = []
    for path in iter_files(root, ["requirements*.txt", "requirements/*.txt"]):
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            parsed = parse_requirement(line)
            if parsed:
                entries.append({
                    "package": parsed[0],
                    "constraints": parsed[1],
                    "where": f"{path.relative_to(root)}:{number}",
                    "file": path,
                })
    return entries


def scan_imports(root):
    local_modules = {p.stem for p in iter_files(root, ["*.py"])}
    local_modules |= {p.name for p in root.rglob("*") if p.is_dir() and (p / "__init__.py").exists()}
    stdlib = set(sys.stdlib_module_names)
    usage = defaultdict(set)
    local_imports = defaultdict(set)
    for path in iter_files(root, ["*.py"]):
        try:
            tree = ast.parse(path.read_text(errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            for module in modules:
                top = module.split(".")[0]
                if top in stdlib:
                    continue
                if top in local_modules:
                    sibling = path.parent / f"{top}.py"
                    if sibling.is_file():
                        local_imports[str(path.relative_to(root))].add(str(sibling.relative_to(root)))
                    continue
                package = MODULE_TO_PACKAGE.get(top, normalize(top))
                usage[package].add(str(path.relative_to(root)))
    for path in iter_files(root, ["*.sh", "*.bash"]):
        for line in path.read_text(errors="replace").splitlines():
            match = re.match(r"^\s*(?:import|from)\s+([A-Za-z_][\w]*)", line)
            if match and match.group(1) not in stdlib and match.group(1) not in local_modules:
                package = MODULE_TO_PACKAGE.get(match.group(1), normalize(match.group(1)))
                usage[package].add(str(path.relative_to(root)))
    return usage, local_imports


def scan_workflow_scripts(root):
    scripts = defaultdict(set)
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        return scripts
    for path in sorted(workflows.glob("*.y*ml")):
        for line in path.read_text(errors="replace").splitlines():
            for match in PYTHON_RUN.finditer(line):
                scripts[str(path.relative_to(root))].add(match.group(1).strip("\"' "))
    return scripts


def version_key(version):
    numbers = []
    for part in re.split(r"[.+!-]", version):
        digits = re.match(r"\d+", part)
        if not digits:
            break
        numbers.append(int(digits.group()))
    return tuple(numbers)


def satisfies(version, constraints):
    current = version_key(version)
    for operator, wanted in constraints:
        target = version_key(wanted.replace(".*", ""))
        width = max(len(current), len(target))
        a = current + (0,) * (width - len(current))
        b = target + (0,) * (width - len(target))
        if operator == "==":
            ok = current[:len(target)] == target if wanted.endswith(".*") else a == b
        elif operator == "!=":
            ok = a != b
        elif operator == ">=":
            ok = a >= b
        elif operator == "<=":
            ok = a <= b
        elif operator == ">":
            ok = a > b
        elif operator == "<":
            ok = a < b
        else:
            ok = a >= b and current[:len(target) - 1] == target[:-1]
        if not ok:
            return False
    return True


def latest_version(package, cache):
    if package not in cache:
        try:
            request = urllib.request.Request(
                f"https://pypi.org/pypi/{package}/json",
                headers={"User-Agent": "check_dependencies.py"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                cache[package] = json.load(response)["info"]["version"]
        except Exception:
            cache[package] = None
    return cache[package]


def format_constraints(constraints):
    return ",".join(f"{op}{version}" for op, version in constraints) or "(any version)"


def is_limited(constraints):
    return any(op in LIMITING for op, _ in constraints)


def with_local_imports(scripts, local_imports):
    seen, queue = set(), list(scripts)
    while queue:
        script = queue.pop()
        if script not in seen:
            seen.add(script)
            queue.extend(local_imports.get(script, ()))
    return seen


def impacted(package, item, usage, local_imports, workflow_scripts, root):
    relative = str(item["file"].relative_to(root))
    users = set(usage.get(package, ()))
    if relative not in workflow_scripts:
        if item["file"].suffix in (".sh", ".bash"):
            return [relative]
        return sorted(users)
    executed = with_local_imports(workflow_scripts[relative], local_imports)
    direct = sorted(executed & users)
    if direct:
        return direct
    return [f"{script} (indirect)" for script in sorted(workflow_scripts[relative])]


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def build_report(root, offline):
    installs = scan_install_commands(root)
    requirement_entries = scan_requirement_files(root)
    usage, local_imports = scan_imports(root)
    workflow_scripts = scan_workflow_scripts(root)
    cache = {}

    def latest(package):
        return None if offline else latest_version(package, cache)

    def status(package, constraints):
        newest = latest(package)
        if newest is None:
            return "?", "not checked"
        if satisfies(newest, constraints):
            return newest, "allows latest"
        return newest, "BLOCKS latest"

    package_installs = [i for i in installs if "package" in i]
    sections = ["# Dependency report", ""]
    outdated = 0

    limited_rows = []
    for item in package_installs + requirement_entries:
        if not is_limited(item["constraints"]):
            continue
        newest, state = status(item["package"], item["constraints"])
        outdated += state == "BLOCKS latest"
        limited_rows.append([
            f"`{item['package']}`",
            f"`{format_constraints(item['constraints'])}`",
            newest,
            state,
            f"`{item['where']}`",
            "<br>".join(impacted(item["package"], item, usage, local_imports, workflow_scripts, root)) or "-",
        ])
    sections += ["## Pinned or capped versions", ""]
    if limited_rows:
        sections += [table(["Package", "Constraint", "Latest on PyPI", "Status", "Declared in", "Impacted"], limited_rows), ""]
    else:
        sections += ["None.", ""]

    grouped = defaultdict(lambda: {"where": [], "impacted": set()})
    for item in package_installs:
        if is_limited(item["constraints"]):
            continue
        group = grouped[(item["package"], format_constraints(item["constraints"]))]
        group["where"].append(item["where"])
        group["impacted"].update(impacted(item["package"], item, usage, local_imports, workflow_scripts, root))
    loose_rows = []
    for (package, constraint), group in sorted(grouped.items()):
        newest = latest(package) or "?"
        loose_rows.append([
            f"`{package}`",
            f"`{constraint}`",
            newest,
            "<br>".join(f"`{where}`" for where in group["where"]),
            "<br>".join(sorted(group["impacted"])) or "-",
        ])
    sections += [
        "## Installed without a fixed version",
        "",
        "These commands take the newest release at every run, so a new upstream version can break them overnight.",
        "",
    ]
    if loose_rows:
        sections += [table(["Package", "Constraint", "Latest on PyPI", "Installed in", "Impacted"], loose_rows), ""]
    else:
        sections += ["None.", ""]

    file_rows = [[f"`{i['requirements_file']}`", f"`{i['where']}`"] for i in installs if "requirements_file" in i]
    if file_rows:
        sections += ["## Requirement files used by install commands", "", table(["File", "Used in"], file_rows), ""]

    declared = {entry["package"] for entry in requirement_entries}
    if requirement_entries:
        rows = []
        for entry in requirement_entries:
            newest, state = status(entry["package"], entry["constraints"])
            users = sorted(usage.get(entry["package"], ()))
            rows.append([
                f"`{entry['package']}`",
                f"`{format_constraints(entry['constraints'])}`",
                newest,
                state,
                len(users) or "**not imported**",
            ])
        sections += [
            "## Requirement files",
            "",
            table(["Package", "Constraint", "Latest on PyPI", "Status", "Scripts importing it"], rows),
            "",
        ]
        missing = sorted(set(usage) - declared)
        if missing:
            rows = [[f"`{name}`", "<br>".join(sorted(usage[name]))] for name in missing]
            sections += ["## Imported but not in a requirement file", "", table(["Package", "Imported by"], rows), ""]

    return "\n".join(sections), outdated


def main():
    parser = argparse.ArgumentParser(description="Report where Python dependencies are pinned and what they impact.")
    parser.add_argument("root", nargs="?", default=".", help="repository root")
    parser.add_argument("--offline", action="store_true", help="do not query PyPI")
    parser.add_argument("--strict", action="store_true", help="exit with status 1 when a constraint blocks the latest version")
    parser.add_argument("--output", help="also write the report to this file")
    args = parser.parse_args()

    report, outdated = build_report(Path(args.root).resolve(), args.offline)
    print(report)
    for target in filter(None, [args.output, os.environ.get("GITHUB_STEP_SUMMARY")]):
        with open(target, "a") as handle:
            handle.write(report + "\n")
    if args.strict and outdated:
        sys.exit(1)


if __name__ == "__main__":
    main()
