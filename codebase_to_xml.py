#!/usr/bin/env python3
"""
XML Codebase Generator - Converts a codebase into an optimized XML structure for AI processing.
This version uses file attributes for metadata and raw code in CDATA for robustness and clarity.
"""

import argparse
import logging
from pathlib import Path
import sys
import fnmatch
from typing import List, Tuple, Optional
import time

# Configuration
DEFAULT_EXCLUDES = [
    '.git', '.vscode', 'node_modules', '__pycache__', 'dist', 'build', 
    '.DS_Store', 'coverage', '.next', 'out', 'logs', '.env', 
    'file_tree.md', '.gitignore', 'codebase.xml', 'modifications.xml', 'backups'
]
BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.eot', '.ttf', '.woff', 
    '.woff2', '.otf', '.zip', '.gz', '.db', '.exe', '.dll', '.so', '.dylib',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
}
LOCK_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']

LANGUAGE_MAP = {
    '.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
    '.cjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.html': 'html',
    '.css': 'css', '.json': 'json', '.xml': 'xml', '.kt': 'kotlin', '.kts': 'kotlin',
    '.java': 'java', '.md': 'markdown', '.sh': 'bash', '.yml': 'yaml', '.yaml': 'yaml',
    '.go': 'go', '.rb': 'ruby', '.php': 'php', '.c': 'c', '.cpp': 'cpp', '.h': 'c',
    '.rs': 'rust', '.swift': 'swift', '.txt': 'text', '.gradle': 'groovy', '.sql': 'sql',
    '.pl': 'perl', '.r': 'r', '.scala': 'scala', '.clj': 'clojure'
}
TREE_CHARS = {"space": "  ", "branch": "|  ", "tee": "├──", "corner": "└──"}

def setup_logging(log_level: str):
    numeric_level = getattr(logging, log_level.upper(), None)
    if not isinstance(numeric_level, int):
        print(f"Invalid log level: {log_level}. Defaulting to INFO.")
        numeric_level = logging.INFO
    logging.basicConfig(level=numeric_level, format='%(levelname)s: %(message)s', stream=sys.stdout)

def parse_arguments():
    parser = argparse.ArgumentParser( # type: ignore
        description="Generate an optimized XML codebase structure for AI processing.", 
        formatter_class=argparse.RawDescriptionHelpFormatter # type: ignore
    ) 
    parser.add_argument("folder_path", type=str, nargs='?', default=".", help="Path to the root folder. Defaults to current directory.")
    parser.add_argument("--depth", type=int, default=10, help="Maximum depth to traverse. Default: 10")
    parser.add_argument("--output", type=str, default="codebase.xml", help="Output XML file name. Default: codebase.xml")
    parser.add_argument("--exclude", type=str, nargs='*', default=[], help="Additional patterns to exclude")
    parser.add_argument("--max-file-size", type=int, default=1024 * 256, help="Maximum file size in bytes. Default: 256KB")
    parser.add_argument("--no-gitignore", action="store_true", help="Ignore .gitignore files")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level. Default: INFO")
    return parser.parse_args()

def parse_gitignore(gitignore_path: Path) -> List[str]:
    if not gitignore_path.is_file(): return []
    patterns = []
    try:
        with open(gitignore_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    patterns.append(line)
    except Exception as e:
        logging.warning(f"Could not read .gitignore at {gitignore_path}: {e}")
    return patterns

def should_exclude(path: Path, base_exclude: List[str], gitignore_patterns: List[Tuple[str, Path]]) -> bool:
    path_name = path.name
    if path_name in base_exclude: return True
    for pattern, gitignore_dir in reversed(gitignore_patterns):
        try:
            rel_path_str = str(path.relative_to(gitignore_dir))
            if fnmatch.fnmatch(rel_path_str, pattern) or fnmatch.fnmatch(path_name, pattern):
                return True
            if pattern.endswith('/') and path.is_dir() and (fnmatch.fnmatch(rel_path_str, pattern[:-1]) or fnmatch.fnmatch(path_name, pattern[:-1])):
                return True
        except ValueError:
            continue
    return False

def get_file_language(file_path: Path) -> str:
    return LANGUAGE_MAP.get(file_path.suffix.lower(), file_path.suffix.lstrip('.'))

def escape_xml_attr(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&apos;')

def write_cdata(content: str) -> str:
    return f"<![CDATA[{content.replace(']]>', ']]]]><![CDATA[>')}]]>"

def generate_tree_summary_string(root_path: Path, depth: int, all_exclude: List[str], no_gitignore: bool) -> str:
    tree_lines = [f"{root_path.name}/"]
    gitignore_cache = {}
    def walk(current_path, prefix="", current_depth=0, parent_patterns: Optional[List[Tuple[str, Path]]] = None):
        if current_depth >= depth: return
        gitignore_patterns = list(parent_patterns) if parent_patterns else []
        if not no_gitignore:
            gitignore_file = current_path / '.gitignore'
            if str(gitignore_file) not in gitignore_cache:
                gitignore_cache[str(gitignore_file)] = parse_gitignore(gitignore_file)
            new_patterns = gitignore_cache[str(gitignore_file)]
            gitignore_patterns.extend([(p, current_path) for p in new_patterns])
        try:
            entries = sorted([p for p in current_path.iterdir()], key=lambda p: (p.is_file(), p.name.lower()))
            filtered_entries = [e for e in entries if not should_exclude(e, all_exclude, gitignore_patterns)]
        except (PermissionError, FileNotFoundError):
            return
        for i, entry in enumerate(filtered_entries):
            connector = TREE_CHARS["corner"] if i == len(filtered_entries) - 1 else TREE_CHARS["tee"]
            tree_lines.append(f"{prefix}{connector} {entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                new_prefix = prefix + (TREE_CHARS["space"] if i == len(filtered_entries) - 1 else TREE_CHARS["branch"])
                walk(entry, new_prefix, current_depth + 1, gitignore_patterns)
    walk(root_path)
    return "\n".join(tree_lines)

def generate_xml_structure(root_path: Path, output_file: str, depth: int, exclude_patterns: List[str], no_gitignore: bool, max_file_size: int):
    root = root_path.resolve()
    all_exclude = DEFAULT_EXCLUDES + exclude_patterns
    gitignore_cache = {}
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<codebase root="{escape_xml_attr(root.name)}">\n')
        
        f.write('  <metadata>\n')
        f.write(f'    <root_path>{escape_xml_attr(str(root))}</root_path>\n')
        f.write('  </metadata>\n')
        
        f.write('  <summary>\n')
        summary_content = generate_tree_summary_string(root, depth, all_exclude, no_gitignore)
        f.write(f'    {write_cdata(summary_content)}\n')
        f.write('  </summary>\n')

        f.write('  <structure>\n')

        def write_file_element(file_path: Path, indent: str):
            relative_path_str = str(file_path.relative_to(root)).replace("\\", "/")
            try:
                file_stat = file_path.stat()
                attrs = {
                    "path": relative_path_str,
                    "language": get_file_language(file_path),
                    "size": str(file_stat.st_size),
                    "last_modified": str(int(file_stat.st_mtime))
                }
                
                content = None
                status = "ok"

                if file_stat.st_size > max_file_size:
                    status = "omitted_large"
                elif file_path.suffix.lower() in BINARY_EXTENSIONS:
                    status = "binary"
                elif file_path.name in LOCK_FILES:
                    status = "lock_file"
                else:
                    try:
                        content = file_path.read_text('utf-8', errors='ignore')
                        if not content.strip():
                            status = "empty"
                    except Exception:
                        status = "read_error"
                
                if status != "ok":
                    attrs["status"] = status

                attr_str = " ".join([f'{key}="{escape_xml_attr(value)}"' for key, value in attrs.items()])
                
                if content and status == "ok":
                    attrs["lines"] = str(content.count('\n') + 1)
                else:
                    attrs["lines"] = "0"
                
                attr_str = " ".join([f'{key}="{escape_xml_attr(value)}"' for key, value in attrs.items()])

                if content and status == "ok":
                    f.write(f'{indent}<file {attr_str}>\n')
                    f.write(f"{indent}  {write_cdata(content)}\n")
                    f.write(f'{indent}</file>\n')
                else:
                    f.write(f'{indent}<file {attr_str} />\n')

            except OSError as e:
                f.write(f'{indent}<file path="{escape_xml_attr(relative_path_str)}" status="access_error" />\n')
        
        def walk_directory(current_path: Path, current_depth: int = 0, parent_patterns: Optional[List[Tuple[str, Path]]] = None, indent: str = "    "):
            if current_depth >= depth: return
            gitignore_patterns = list(parent_patterns) if parent_patterns else []
            if not no_gitignore:
                gitignore_file = current_path / '.gitignore'
                if str(gitignore_file) not in gitignore_cache:
                    gitignore_cache[str(gitignore_file)] = parse_gitignore(gitignore_file)
                new_patterns = gitignore_cache[str(gitignore_file)]
                gitignore_patterns.extend([(p, current_path) for p in new_patterns])
            
            try:
                entries = sorted([p for p in current_path.iterdir()], key=lambda p: (p.is_file(), p.name.lower()))
                filtered_entries = [e for e in entries if not should_exclude(e, all_exclude, gitignore_patterns)]
            except (PermissionError, FileNotFoundError) as e:
                logging.warning(f"Could not access {current_path}: {e}")
                return
            
            for entry in filtered_entries:
                if entry.is_dir():
                    f.write(f'{indent}<directory name="{escape_xml_attr(entry.name)}">\n')
                    walk_directory(entry, current_depth + 1, gitignore_patterns, indent + "  ")
                    f.write(f'{indent}</directory>\n')
                else:
                    write_file_element(entry, indent)
        
        walk_directory(root)
        
        f.write('  </structure>\n')
        f.write('</codebase>\n')
    
    logging.info(f"XML codebase structure written to {output_file}")

def main():
    args = parse_arguments()
    setup_logging(args.log_level)
    
    root_path = Path(args.folder_path)
    if not root_path.is_dir():
        logging.error(f"Error: Path '{args.folder_path}' is not a valid directory.")
        sys.exit(1)
    
    logging.info(f"Generating XML structure for '{root_path.name}' (depth: {args.depth})...")
    
    try:
        generate_xml_structure(
            root_path=root_path,
            output_file=args.output,
            depth=args.depth,
            exclude_patterns=args.exclude,
            no_gitignore=args.no_gitignore,
            max_file_size=args.max_file_size
        )
        logging.info(f"XML generation completed successfully. Output saved to '{args.output}'.")
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
