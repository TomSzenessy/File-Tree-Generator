#!/usr/bin/env python3
"""
Web Interface for Codebase Analysis and XML Generation
Provides a visual interface to:
- Browse folder structure
- View file statistics (size, lines, tokens)
- Select/deselect files and folders
- Save/restore selection configuration
- Generate XML with selected files only
"""

import os
import json
import fnmatch
import mimetypes
import subprocess
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from flask import Flask, render_template_string, jsonify, request, send_file

# Configuration
BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.eot', '.ttf', '.woff', 
    '.woff2', '.otf', '.zip', '.gz', '.db', '.exe', '.dll', '.so', '.dylib',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.mp4', '.mp3',
    '.avi', '.mov', '.wmv', '.flv', '.mkv', '.webm', '.wav', '.flac'
}

DEFAULT_EXCLUDES = [
    '.git', '.vscode', 'node_modules', '__pycache__', 'dist', 'build',
    '.DS_Store', 'coverage', '.next', 'out', 'logs', '.env',
    'codebase.xml', 'modifications.xml', 'backups', '.swc',
    # Keep in sync with codebase_to_xml.py
    'file_tree.md', 'gemini_system_prompt.md', '.gitignore', 'codebase_to_xml.py',
    'apply_xml_changes.py', 'sw.js', 'tsconfig.jest.tsbuildinfo', 'tree_gen.py',
    '.codebase_exclude_temp'
]

CONFIG_FILE = 'codebase_selection.json'
TOKENS_PER_CHAR_ESTIMATE = 0.31748
TREE_CHARS = {"space": "  ", "branch": "|  ", "tee": "├──", "corner": "└──"}

# Language mapping for XML attributes
LANGUAGE_MAP = {
    '.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
    '.cjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.html': 'html',
    '.css': 'css', '.json': 'json', '.xml': 'xml', '.kt': 'kotlin', '.kts': 'kotlin',
    '.java': 'java', '.md': 'markdown', '.sh': 'bash', '.yml': 'yaml', '.yaml': 'yaml',
    '.go': 'go', '.rb': 'ruby', '.php': 'php', '.c': 'c', '.cpp': 'cpp', '.h': 'c',
    '.rs': 'rust', '.swift': 'swift', '.txt': 'text', '.gradle': 'groovy', '.sql': 'sql',
    '.pl': 'perl', '.r': 'r', '.scala': 'scala', '.clj': 'clojure'
}

app = Flask(__name__)

def escape_xml_attr(text: str) -> str:
    """Escape XML attribute values"""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&apos;')

def write_cdata(content: str) -> str:
    """Write content as CDATA section"""
    return f"<![CDATA[{content.replace(']]>', ']]]]><![CDATA[>')}]]>"

def get_file_language(file_path: Path) -> str:
    """Get language identifier for file based on extension"""
    return LANGUAGE_MAP.get(file_path.suffix.lower(), file_path.suffix.lstrip('.'))

# Constants that match the external XML generator exactly
LOCK_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']
BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.eot', '.ttf', '.woff', 
    '.woff2', '.otf', '.zip', '.gz', '.db', '.exe', '.dll', '.so', '.dylib',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
}
CONTENT_EXCLUDE_PATTERNS = {
    'mock_files': ['__mocks__'],
}
HIGH_VALUE_FILES = {
    'configs': ['next.config.', 'tailwind.config.', 'jest.config.', 'eslint.config.', 'tsconfig.', 'middleware.'],
    'schema': ['schema.prisma'],
    'docs': ['.md', '.txt'],
    'package': ['package.json'],  # Note: not package-lock.json
    'tests': ['.test.', '.spec.', '__tests__/'],  # INCLUDE test files - crucial for AI context
}

def is_high_value_file(file_path: Path) -> bool:
    """Check if file is high-value for AI analysis and should always include content."""
    file_name = file_path.name
    relative_path = str(file_path).replace("\\", "/")
    
    for patterns in HIGH_VALUE_FILES.values():
        for pattern in patterns:
            if pattern in file_name or pattern in relative_path:
                return True
    
    return False

def should_skip_directory_entirely(dir_path: Path, root_path: Path) -> bool:
    """Mirror tools/codebase_to_xml.py logic: skip certain directories entirely."""
    try:
        relative_path = str(dir_path.relative_to(root_path)).replace("\\", "/")
    except ValueError:
        relative_path = str(dir_path).replace("\\", "/")

    # Skip migration directories entirely
    for pattern in CONTENT_EXCLUDE_PATTERNS.get('migrations', ['prisma/migrations']):
        if pattern in relative_path:
            return True

    # Skip non-primary locale directories (keep only 'en')
    if 'locales/' in relative_path and not relative_path.endswith('locales/en'):
        parent_parts = relative_path.split('/')
        if 'locales' in parent_parts:
            locale_index = parent_parts.index('locales')
            if locale_index + 1 < len(parent_parts) and parent_parts[locale_index + 1] != 'en':
                return True

    return False

def will_file_be_skipped_in_xml(file_path: Path, file_info: 'FileInfo') -> Tuple[bool, str]:
    """Determine if a file will have content in XML generation (not if it's included)
    
    This logic exactly matches codebase_to_xml.py to ensure perfect sync.
    Note: ALL files are included in XML, but only some have content.
    This function determines if the file will have actual content (status == 'ok')
    or just be an empty tag with metadata.
    
    Logic order matches codebase_to_xml.py exactly:
    1. should_exclude_file_content() check (but high-value files override)
    2. Large file size check (>256KB) 
    3. Binary extension check
    4. Lock files check
    5. Empty/readable check
    """
    relative_path_str = str(file_path).replace("\\", "/")
    
    # First check: should_exclude_file_content() equivalent
    # Always include high-value files (this overrides everything else!)
    if is_high_value_file(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                if not content.strip():
                    return True, "empty"
            return False, "ok"
        except Exception:
            return True, "read_error"
    
    # Exclude mock file content  
    for pattern in CONTENT_EXCLUDE_PATTERNS['mock_files']:
        if pattern in relative_path_str:
            return True, "mock_file_excluded"
    
    # Second check: Large files get added to XML but without content (this comes BEFORE lock file check!)
    if file_info.size > 1024 * 256:  # 256KB limit matches codebase_to_xml.py
        return True, "omitted_large"
    
    # Third check: Binary files get added to XML but without content  
    if file_path.suffix.lower() in BINARY_EXTENSIONS:
        return True, "binary"
    
    # Fourth check: Lock files get added to XML but without content (only for small lock files)
    if file_path.name in LOCK_FILES:
        return True, "lock_file_excluded"
    
    # Fifth check: Check if file is readable and has content
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            if not content.strip():
                return True, "empty"
    except Exception:
        return True, "read_error"
    
    # File will have content in XML
    return False, "ok"

def generate_tree_summary_string(root_path: Path, excluded_paths: Set[str], gitignore_patterns: List[Tuple[str, Path]], custom_excludes: List[str]) -> str:
    """Generate a tree structure string for selected files only"""
    tree_lines = [f"{root_path.name}/"]
    
    def should_exclude_path(path: Path) -> bool:
        """Check if path should be excluded"""
        if path.name in custom_excludes:
            return True
        
        rel_path = str(path.relative_to(root_path)) if path != root_path else '.'
        if rel_path in excluded_paths:
            return True
            
        for pattern, gitignore_dir in gitignore_patterns:
            try:
                rel_to_gitignore = path.relative_to(gitignore_dir)
                if fnmatch.fnmatch(str(rel_to_gitignore), pattern) or fnmatch.fnmatch(path.name, pattern):
                    return True
            except ValueError:
                continue
        return False
    
    def walk(current_path, prefix="", current_depth=0):
        if current_depth >= 10:  # Limit tree depth for readability
            return
        
        try:
            entries = sorted([p for p in current_path.iterdir()], key=lambda p: (p.is_file(), p.name.lower()))
            # Filter to only include non-excluded entries
            filtered_entries = [e for e in entries if not should_exclude_path(e)]
        except (PermissionError, FileNotFoundError):
            return
        
        for i, entry in enumerate(filtered_entries):
            connector = TREE_CHARS["corner"] if i == len(filtered_entries) - 1 else TREE_CHARS["tee"]
            tree_lines.append(f"{prefix}{connector} {entry.name}{'/' if entry.is_dir() else ''}")
            
            if entry.is_dir():
                new_prefix = prefix + (TREE_CHARS["space"] if i == len(filtered_entries) - 1 else TREE_CHARS["branch"])
                walk(entry, new_prefix, current_depth + 1)
    
    walk(root_path)
    return "\n".join(tree_lines)

@dataclass
class FileInfo:
    """Information about a single file"""
    path: str
    name: str
    size: int
    lines: int
    characters: int
    estimated_tokens: int
    is_binary: bool
    is_text: bool
    extension: str
    will_have_content: bool = True
    content_status: str = "ok"

@dataclass
class FolderMetrics:
    """Cached metrics for a folder including recursive totals"""
    total_size: int = 0
    total_lines: int = 0
    total_characters: int = 0
    total_tokens: int = 0
    file_count: int = 0
    text_file_count: int = 0
    recursive_size: int = 0
    recursive_lines: int = 0
    recursive_characters: int = 0
    recursive_tokens: int = 0
    recursive_file_count: int = 0
    recursive_text_file_count: int = 0
    depth: int = 0

@dataclass
class FolderInfo:
    """Information about a folder and its contents"""
    path: str
    name: str
    total_size: int
    total_lines: int
    total_characters: int
    total_tokens: int
    file_count: int
    text_file_count: int
    files: List[FileInfo]
    subfolders: List[str]
    recursive_size: int = 0
    recursive_lines: int = 0
    recursive_characters: int = 0
    recursive_tokens: int = 0
    recursive_file_count: int = 0
    recursive_text_file_count: int = 0
    depth: int = 0

class CodebaseAnalyzer:
    def __init__(self, root_path: str, max_depth: int = 12):
        self.root_path = Path(root_path).resolve()
        self.max_depth = max_depth
        self.gitignore_patterns = self._parse_gitignore()
        self.excluded_paths: Set[str] = set()
        self.custom_excludes: List[str] = DEFAULT_EXCLUDES.copy()
        self.folder_metrics_cache: Dict[str, FolderMetrics] = {}
        self.cache_lock = threading.RLock()
        
        # Initialize depth scanning
        print(f"Initializing folder metrics cache (depth {max_depth})...")
        self._initialize_folder_cache()
        
    def _parse_gitignore(self) -> List[Tuple[str, Path]]:
        """Parse .gitignore files recursively"""
        patterns = []
        for gitignore_path in self.root_path.rglob('.gitignore'):
            try:
                with open(gitignore_path, 'r', encoding='utf-8', errors='ignore') as f:
                    gitignore_dir = gitignore_path.parent
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#'):
                            patterns.append((line, gitignore_dir))
            except Exception:
                pass
        return patterns
    
    def _should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded based on gitignore and custom patterns"""
        if path.name in self.custom_excludes:
            return True
            
        for pattern, gitignore_dir in self.gitignore_patterns:
            try:
                rel_path = path.relative_to(gitignore_dir)
                if fnmatch.fnmatch(str(rel_path), pattern) or fnmatch.fnmatch(path.name, pattern):
                    return True
            except ValueError:
                continue

        # Align with XML generator's AI optimization directory skips
        try:
            if path.is_dir() and should_skip_directory_entirely(path, self.root_path):
                return True
        except Exception:
            pass
        
        return False
    
    def _initialize_folder_cache(self) -> None:
        """Initialize folder metrics cache by scanning all folders up to max_depth"""
        scanned_folders = 0
        excluded_folders = 0
        
        def scan_folder(folder_path: Path, current_depth: int = 0) -> FolderMetrics:
            nonlocal scanned_folders, excluded_folders
            
            if current_depth > self.max_depth:
                return FolderMetrics(depth=current_depth)
            
            rel_path = str(folder_path.relative_to(self.root_path)) if folder_path != self.root_path else '.'
            
            # Skip if already cached
            if rel_path in self.folder_metrics_cache:
                return self.folder_metrics_cache[rel_path]
            
            # Check if should be excluded
            if self._should_exclude(folder_path):
                excluded_folders += 1
                empty_metrics = FolderMetrics(depth=current_depth)
                with self.cache_lock:
                    self.folder_metrics_cache[rel_path] = empty_metrics
                return empty_metrics
            
            scanned_folders += 1
            metrics = FolderMetrics(depth=current_depth)
            
            try:
                # Get all entries first
                entries = sorted(folder_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
                files_processed = 0
                files_excluded = 0
                files_failed = 0
                
                for entry in entries:
                    if entry.is_file():
                        entry_rel_path = str(entry.relative_to(self.root_path))
                        
                        # Skip excluded files (same logic as analyze_folder)
                        if self._should_exclude(entry) or entry_rel_path in self.excluded_paths:
                            files_excluded += 1
                            continue
                            
                        file_info = self._analyze_file(entry)
                        if file_info:
                            files_processed += 1
                            metrics.file_count += 1
                            metrics.total_size += file_info.size
                            
                            # Only count lines/tokens for files that will have content in XML
                            if file_info.will_have_content and file_info.is_text:
                                metrics.text_file_count += 1
                                metrics.total_lines += file_info.lines
                                metrics.total_characters += file_info.characters
                                metrics.total_tokens += file_info.estimated_tokens
                        else:
                            files_failed += 1
                    
                    elif entry.is_dir() and current_depth < self.max_depth:
                        # Always scan subfolders, even if they might be excluded
                        subfolder_metrics = scan_folder(entry, current_depth + 1)
                        
                        # Only add to recursive totals if subfolder is not excluded
                        if not self._should_exclude(entry):
                            metrics.recursive_size += subfolder_metrics.total_size + subfolder_metrics.recursive_size
                            metrics.recursive_lines += subfolder_metrics.total_lines + subfolder_metrics.recursive_lines
                            metrics.recursive_characters += subfolder_metrics.total_characters + subfolder_metrics.recursive_characters
                            metrics.recursive_tokens += subfolder_metrics.total_tokens + subfolder_metrics.recursive_tokens
                            metrics.recursive_file_count += subfolder_metrics.file_count + subfolder_metrics.recursive_file_count
                            metrics.recursive_text_file_count += subfolder_metrics.text_file_count + subfolder_metrics.recursive_text_file_count
                
            
            except PermissionError:
                print(f"Permission denied: {folder_path}")
            except Exception as e:
                print(f"Error scanning {folder_path}: {e}")
            
            # Always cache the metrics (even for empty folders)
            with self.cache_lock:
                self.folder_metrics_cache[rel_path] = metrics
            
            return metrics
        
        # Start scanning from root
        print("Initializing folder cache...")
        scan_folder(self.root_path)
        
        print(f"Cached metrics for {len(self.folder_metrics_cache)} folders ({scanned_folders} scanned, {excluded_folders} excluded)")
    
    def get_folder_metrics(self, folder_path: str) -> Optional[FolderMetrics]:
        """Get cached folder metrics"""
        with self.cache_lock:
            return self.folder_metrics_cache.get(folder_path)
    
    def _is_binary(self, file_path: Path) -> bool:
        """Check if file is binary"""
        if file_path.suffix.lower() in BINARY_EXTENSIONS:
            return True
       
        """ mime_type, _ = mimetypes.guess_type(str(file_path))
        if mime_type and not mime_type.startswith('text'):
            return True """
            
        return False
    
    def _analyze_file(self, file_path: Path) -> Optional[FileInfo]:
        """Analyze a single file and return its info"""
        try:
            stat = file_path.stat()
            is_binary = self._is_binary(file_path)
            
            lines = 0
            characters = 0
            
            if not is_binary and stat.st_size < 100 * 1024 * 1024:  # Max 10MB for text analysis
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        lines = content.count('\n') + 1
                        characters = len(content)
                except Exception as e:
                    # If we can't read as text, treat as binary
                    is_binary = True
            
            estimated_tokens = int(characters * TOKENS_PER_CHAR_ESTIMATE)
            
            # Calculate relative path safely
            try:
                rel_path = str(file_path.relative_to(self.root_path))
            except ValueError:
                # If relative path calculation fails, use absolute path
                rel_path = str(file_path)
            
            # Create initial FileInfo
            file_info = FileInfo(
                path=rel_path,
                name=file_path.name,
                size=stat.st_size,
                lines=lines,
                characters=characters,
                estimated_tokens=estimated_tokens,
                is_binary=is_binary,
                is_text=not is_binary,
                extension=file_path.suffix
            )
            
            # Check if this file will have content in XML generation
            will_skip_content, content_status = will_file_be_skipped_in_xml(file_path, file_info)
            file_info.will_have_content = not will_skip_content
            file_info.content_status = content_status
            
            return file_info
        except Exception as e:
            print(f"ERROR analyzing file {file_path}: {e}")
            return None
    
    def analyze_folder(self, folder_path: Optional[str] = None) -> FolderInfo:
        """Analyze a folder and return comprehensive information with cached metrics"""
        if folder_path:
            target_path = self.root_path / folder_path
        else:
            target_path = self.root_path
        
        if not target_path.exists() or not target_path.is_dir():
            raise ValueError(f"Invalid folder path: {folder_path}")
        
        rel_path = str(target_path.relative_to(self.root_path)) if folder_path else "."
        
        # Get cached metrics
        cached_metrics = self.get_folder_metrics(rel_path)
        if cached_metrics is None:
            cached_metrics = FolderMetrics()
        
        files = []
        subfolders = []
        total_size = 0
        total_lines = 0
        total_characters = 0
        total_tokens = 0
        text_file_count = 0
        
        try:
            for entry in sorted(target_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if self._should_exclude(entry):
                    continue
                    
                entry_rel_path = str(entry.relative_to(self.root_path))
                is_excluded = entry_rel_path in self.excluded_paths
                
                if entry.is_file():
                    file_info = self._analyze_file(entry)
                    if file_info:
                        files.append(file_info)
                        # Only count non-excluded files in totals
                        if not is_excluded:
                            total_size += file_info.size
                            # Only count lines/tokens for files that will have content in XML
                            if file_info.will_have_content and file_info.is_text:
                                total_lines += file_info.lines
                                total_characters += file_info.characters
                                total_tokens += file_info.estimated_tokens
                                text_file_count += 1
                elif entry.is_dir():
                    subfolders.append(entry.name)
        except PermissionError:
            pass
        
        return FolderInfo(
            path=rel_path,
            name=target_path.name,
            total_size=total_size,
            total_lines=total_lines,
            total_characters=total_characters,
            total_tokens=total_tokens,
            file_count=len(files),
            text_file_count=text_file_count,
            files=files,
            subfolders=subfolders,
            recursive_size=cached_metrics.recursive_size,
            recursive_lines=cached_metrics.recursive_lines,
            recursive_characters=cached_metrics.recursive_characters,
            recursive_tokens=cached_metrics.recursive_tokens,
            recursive_file_count=cached_metrics.recursive_file_count,
            recursive_text_file_count=cached_metrics.recursive_text_file_count,
            depth=cached_metrics.depth
        )
    
    def get_tree_structure(self) -> Dict:
        """Get complete tree structure with selection states"""
        def build_tree(path: Path, relative_path: str = "") -> Dict:
            metrics = self.get_folder_metrics(relative_path if relative_path else ".")
            result = {
                "name": path.name if path != self.root_path else self.root_path.name,
                "path": relative_path if relative_path else ".",
                "type": "folder",
                "selected": relative_path not in self.excluded_paths,
                "children": [],
                "recursive_size": metrics.recursive_size if metrics else 0,
                "recursive_lines": metrics.recursive_lines if metrics else 0,
                "recursive_tokens": metrics.recursive_tokens if metrics else 0,
                "recursive_file_count": metrics.recursive_file_count if metrics else 0,
                "total_size": metrics.total_size if metrics else 0,
                "total_lines": metrics.total_lines if metrics else 0,
                "total_tokens": metrics.total_tokens if metrics else 0,
                "file_count": metrics.file_count if metrics else 0,
                "depth": metrics.depth if metrics else 0
            }
            
            try:
                for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                    if self._should_exclude(entry):
                        continue
                    
                    child_relative = str(entry.relative_to(self.root_path))
                    
                    if entry.is_dir():
                        result["children"].append(build_tree(entry, child_relative))
                    else:
                        file_info = self._analyze_file(entry)
                        if file_info:
                            result["children"].append({
                                "name": entry.name,
                                "path": child_relative,
                                "type": "file",
                                "selected": child_relative not in self.excluded_paths,
                                "size": file_info.size,
                                "lines": file_info.lines,
                                "tokens": file_info.estimated_tokens,
                                "is_binary": file_info.is_binary,
                                "will_have_content": file_info.will_have_content,
                                "content_status": file_info.content_status
                            })
            except PermissionError:
                pass
            
            return result
        
        return build_tree(self.root_path)
    
    def save_configuration(self) -> None:
        """Save current selection configuration to JSON"""
        config = {
            "timestamp": datetime.now().isoformat(),
            "root_path": str(self.root_path),
            "excluded_paths": list(self.excluded_paths),
            "custom_excludes": self.custom_excludes
        }
        
        config_path = self.root_path / CONFIG_FILE
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
    
    def load_configuration(self) -> bool:
        """Load selection configuration from JSON"""
        config_path = self.root_path / CONFIG_FILE
        if not config_path.exists():
            return False
        
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            self.excluded_paths = set(config.get('excluded_paths', []))
            self.custom_excludes = config.get('custom_excludes', DEFAULT_EXCLUDES.copy())
            return True
        except Exception:
            return False
    
    def generate_xml(self, output_file: str = "codebase.xml") -> str:
        """Generate XML using the codebase_to_xml.py script with selected files"""
        # Create a temporary exclusion file
        temp_exclude_file = self.root_path / ".codebase_exclude_temp"
        
        try:
            # Write excluded paths to temporary file
            with open(temp_exclude_file, 'w') as f:
                for path in self.excluded_paths:
                    f.write(f"{path}\n")
            
            # Build command to run codebase_to_xml.py
            cmd = [
                sys.executable,
                "codebase_to_xml.py",
                str(self.root_path),
                "--output", output_file
            ]
            
            # Add custom excludes
            if self.custom_excludes:
                cmd.extend(["--exclude"] + self.custom_excludes)
            
            # Add excluded paths as specific files to exclude
            if self.excluded_paths:
                cmd.extend(["--exclude-files"] + list(self.excluded_paths))
            
            # Run the command
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=self.root_path)
            
            if result.returncode != 0:
                raise Exception(f"XML generation failed: {result.stderr}")
            
            return os.path.join(self.root_path, output_file)
            
        finally:
            # Clean up temporary file
            if temp_exclude_file.exists():
                temp_exclude_file.unlink()

# Global analyzer instance
analyzer = None

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Codebase Analyzer</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: #1a1a1a;
            color: #e0e0e0;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        
        .header {
            background: #2d2d2d;
            padding: 1rem;
            border-bottom: 1px solid #444;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        
        .header h1 {
            font-size: 1.5rem;
            color: #4a9eff;
        }
        
        .controls {
            display: flex;
            gap: 1rem;
            align-items: center;
        }
        
        .stats {
            background: #252525;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            font-size: 0.9rem;
            display: flex;
            gap: 1rem;
        }
        
        .stats .stat-group {
            display: flex;
            flex-direction: column;
            align-items: center;
            text-align: center;
        }
        
        .stats .stat-label {
            font-size: 0.7rem;
            color: #888;
            margin-bottom: 2px;
        }
        
        .stats .stat-value {
            color: #4a9eff;
            font-weight: bold;
        }
        
        button {
            background: #4a9eff;
            color: white;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9rem;
            transition: background 0.2s;
        }
        
        button:hover {
            background: #3a8eef;
        }
        
  
        
        /* Styles for excluded/deselected items */
        .excluded-item {
            opacity: 0.5;
            background-color: #f8f9fa;
        }
        
        .excluded-item td {
            color: #6c757d;
            font-style: italic;
        }
        
        .excluded-item .folder-name,
        .excluded-item .file-name {
            text-decoration: line-through;
        }
        
        .tree-node.excluded {
            opacity: 0.6;
            color: #6c757d;
        }
        
        .tree-node.excluded .tree-checkbox {
            opacity: 0.7;
        }
        
        .container {
            display: flex;
            flex: 1;
            overflow: hidden;
        }
        
        .sidebar {
            width: 350px;
            background: #252525;
            border-right: 1px solid #444;
            overflow-y: auto;
            padding: 1rem;
        }
        
        .main {
            flex: 1;
            overflow-y: auto;
            padding: 1rem;
        }
        
        .tree-item {
            margin: 2px 0;
            user-select: none;
        }
        
        .tree-item.folder > .tree-label {
            font-weight: bold;
            color: #4a9eff;
        }
        
        .tree-item.file > .tree-label {
            color: #e0e0e0;
        }
        
        .tree-label {
            display: flex;
            align-items: center;
            padding: 4px;
            cursor: pointer;
            border-radius: 3px;
        }
        
        .tree-label:hover {
            background: #333;
        }
        
        .tree-label input[type="checkbox"] {
            margin-right: 8px;
        }
        
        .tree-label .icon {
            margin-right: 6px;
            width: 16px;
            text-align: center;
        }
        
        .tree-children {
            margin-left: 20px;
            display: none;
        }
        
        .tree-item.expanded > .tree-children {
            display: block;
        }
        
        .tree-item.excluded {
            opacity: 0.5;
        }
        
        .tree-item.excluded .tree-label {
            text-decoration: line-through;
        }
        
        .file-info {
            font-size: 0.8rem;
            color: #888;
            margin-left: 4px;
        }
        
        .current-folder {
            background: #2d2d2d;
            padding: 1rem;
            border-radius: 4px;
            margin-bottom: 1rem;
        }
        
        .current-folder h2 {
            color: #4a9eff;
            margin-bottom: 0.5rem;
        }
        
        .folder-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
        }
        
        .stat-card {
            background: #333;
            padding: 0.75rem;
            border-radius: 4px;
        }
        
        .stat-card .label {
            color: #888;
            font-size: 0.8rem;
        }
        
        .stat-card .value {
            color: #4a9eff;
            font-size: 1.2rem;
            font-weight: bold;
        }
        
        .file-list {
            background: #2d2d2d;
            border-radius: 4px;
            padding: 1rem;
        }
        
        .file-list h3 {
            color: #4a9eff;
            margin-bottom: 1rem;
        }
        
        .file-table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .file-table th {
            text-align: left;
            padding: 0.5rem;
            border-bottom: 1px solid #444;
            color: #888;
        }
        
        .file-table td {
            padding: 0.5rem;
            border-bottom: 1px solid #333;
        }
        
        .file-table tr:hover {
            background: #333;
        }
        
        .binary-badge {
            background: #ff6b6b;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.7rem;
        }
        
        .skip-badge {
            background: #ffc107;
            color: #212529;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.7rem;
            cursor: help;
        }
        
        .no-content-file {
            background-color: #fff3cd;
            border-left: 3px solid #ffc107;
        }
        
        .no-content-file td {
            color: #856404;
            font-style: italic;
        }
        
        .text-badge {
            background: #51cf66;
            color: white;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 0.7rem;
        }
        
        .loading {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.8);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        
        .loading.hidden {
            display: none;
        }
        
        .spinner {
            border: 3px solid #333;
            border-top: 3px solid #4a9eff;
            border-radius: 50%;
            width: 40px;
            height: 40px;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .breadcrumb {
            padding: 0.5rem;
            background: #333;
            border-radius: 4px;
            margin-bottom: 1rem;
        }
        
        .breadcrumb a {
            color: #4a9eff;
            text-decoration: none;
            padding: 0 0.25rem;
        }
        
        .breadcrumb a:hover {
            text-decoration: underline;
        }
        
        .breadcrumb span {
            color: #666;
            padding: 0 0.25rem;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📁 Codebase Analyzer</h1>
        <div class="controls">
            <div class="stats">
                <div class="stat-group">
                    <div class="stat-label">Selected</div>
                    <div class="stat-value" id="totalSize">0 MB</div>
                </div>
                <div class="stat-group">
                    <div class="stat-label">Lines</div>
                    <div class="stat-value" id="totalLines">0</div>
                </div>
                <div class="stat-group">
                    <div class="stat-label">Tokens</div>
                    <div class="stat-value" id="totalTokens">0</div>
                </div>
            </div>
            <button onclick="selectAll()">Select All</button>
            <button onclick="deselectAll()">Deselect All</button>
            <button onclick="saveConfig()">💾 Save Config</button>
            <button onclick="loadConfig()">📂 Load Config</button>
            <button onclick="generateXML()" id="generateBtn">🚀 Generate XML</button>
        </div>
    </div>
    
    <div class="container">
        <div class="sidebar">
            <h3 style="margin-bottom: 1rem;">File Tree</h3>
            <div id="fileTree"></div>
        </div>
        
        <div class="main">
            <div class="breadcrumb" id="breadcrumb">
                <a href="#" onclick="navigateToFolder(''); return false;">Root</a>
            </div>
            
            <div class="current-folder">
                <h2 id="currentFolderName">Root</h2>
                <div class="folder-stats" id="folderStats"></div>
            </div>
            
            <div class="file-list">
                <h3>Files in Current Folder</h3>
                <table class="file-table">
                    <thead>
                        <tr>
                            <th>Name</th>
                            <th>Type</th>
                            <th>Size</th>
                            <th>Lines</th>
                            <th>Tokens</th>
                            <th>Include</th>
                        </tr>
                    </thead>
                    <tbody id="fileTableBody"></tbody>
                </table>
            </div>
        </div>
    </div>
    
    <div class="loading hidden" id="loading">
        <div class="spinner"></div>
    </div>
    
    <script>
        let treeData = null;
        let currentPath = '';
        let excludedPaths = new Set();
        
        function showLoading() {
            document.getElementById('loading').classList.remove('hidden');
        }
        
        function hideLoading() {
            document.getElementById('loading').classList.add('hidden');
        }
        
        function formatSize(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }
        
        function formatNumber(num) {
            return num.toLocaleString();
        }
        
        function findFolderInTree(node, targetPath) {
            if (node.path === targetPath && node.type === 'folder') {
                return node;
            }
            
            if (node.children) {
                for (const child of node.children) {
                    const result = findFolderInTree(child, targetPath);
                    if (result) return result;
                }
            }
            
            return null;
        }
        
        async function loadTree() {
            showLoading();
            try {
                const response = await fetch('/api/tree');
                treeData = await response.json();
                renderTree();
                updateStats();
            } catch (error) {
                alert('Error loading tree: ' + error.message);
            } finally {
                hideLoading();
            }
        }
        
        function renderTree() {
            const container = document.getElementById('fileTree');
            container.innerHTML = renderTreeNode(treeData);
        }
        
        function renderTreeNode(node, level = 0) {
            const isFolder = node.type === 'folder';
            const isExcluded = excludedPaths.has(node.path);
            const hasChildren = node.children && node.children.length > 0;
            
            let html = `<div class="tree-item tree-node ${isFolder ? 'folder' : 'file'} ${isExcluded ? 'excluded' : ''}" data-path="${node.path}">`;
            html += `<div class="tree-label" onclick="toggleNode(event, '${node.path}')">`;
            
            if (isFolder) {
                html += `<span class="icon">${hasChildren ? '▶' : '○'}</span>`;
            } else {
                html += `<span class="icon">📄</span>`;
            }
            
            html += `<input type="checkbox" class="tree-checkbox" ${!isExcluded ? 'checked' : ''} onclick="toggleSelection(event, '${node.path}')">`;
            html += `<span>${node.name}</span>`;
            
            if (!isFolder) {
                html += `<span class="file-info">(${formatSize(node.size)}, ${node.lines} lines)</span>`;
            } else {
                const totalFiles = (node.file_count || 0) + (node.recursive_file_count || 0);
                const totalSize = (node.total_size || 0) + (node.recursive_size || 0);
                if (totalFiles > 0) {
                    html += `<span class="file-info">(${totalFiles} files, ${formatSize(totalSize)}, d:${node.depth})</span>`;
                }
            }
            
            html += `</div>`;
            
            if (hasChildren) {
                html += `<div class="tree-children">`;
                for (const child of node.children) {
                    html += renderTreeNode(child, level + 1);
                }
                html += `</div>`;
            }
            
            html += `</div>`;
            return html;
        }
        
        function toggleNode(event, path) {
            event.stopPropagation();
            const item = document.querySelector(`.tree-item[data-path="${path}"]`);
            if (item && item.classList.contains('folder')) {
                item.classList.toggle('expanded');
                const icon = item.querySelector('.icon');
                if (icon) {
                    icon.textContent = item.classList.contains('expanded') ? '▼' : '▶';
                }
                
                // Navigate to folder
                if (item.classList.contains('expanded')) {
                    navigateToFolder(path);
                }
            }
        }
        
        function toggleSelection(event, path) {
            event.stopPropagation();
            
            if (event.target.checked) {
                excludedPaths.delete(path);
                // Remove all children from excluded
                removeChildrenFromExcluded(path);
            } else {
                excludedPaths.add(path);
                // Add all children to excluded
                addChildrenToExcluded(path);
            }
            
            renderTree();
            updateStats();
            updateFolderView();
        }
        
        function removeChildrenFromExcluded(parentPath) {
            const toRemove = [];
            for (const path of excludedPaths) {
                if (path.startsWith(parentPath + '/') || path.startsWith(parentPath + '\\\\')) {
                    toRemove.push(path);
                }
            }
            toRemove.forEach(path => excludedPaths.delete(path));
        }
        
        function addChildrenToExcluded(parentPath) {
            function addChildren(node) {
                if (node.path.startsWith(parentPath)) {
                    excludedPaths.add(node.path);
                    if (node.children) {
                        node.children.forEach(addChildren);
                    }
                }
            }
            
            function findNode(node, targetPath) {
                if (node.path === targetPath) {
                    if (node.children) {
                        node.children.forEach(addChildren);
                    }
                    return true;
                }
                if (node.children) {
                    for (const child of node.children) {
                        if (findNode(child, targetPath)) return true;
                    }
                }
                return false;
            }
            
            findNode(treeData, parentPath);
        }
        
        function selectAll() {
            excludedPaths.clear();
            renderTree();
            updateStats();
            updateFolderView();
        }
        
        function deselectAll() {
            function addAll(node) {
                excludedPaths.add(node.path);
                if (node.children) {
                    node.children.forEach(addAll);
                }
            }
            addAll(treeData);
            renderTree();
            updateStats();
            updateFolderView();
        }
        
        async function navigateToFolder(path) {
            currentPath = path;
            showLoading();
            try {
                const response = await fetch(`/api/folder?path=${encodeURIComponent(path)}`);
                const data = await response.json();
                updateFolderView(data);
                updateBreadcrumb(path);
            } catch (error) {
                alert('Error loading folder: ' + error.message);
            } finally {
                hideLoading();
            }
        }
        
        function updateBreadcrumb(path) {
            const breadcrumb = document.getElementById('breadcrumb');
            let html = '<a href="#" onclick="navigateToFolder(\\'\\'); return false;">Root</a>';
            
            if (path) {
                const parts = path.split(/[\\\\/]/);
                let currentPath = '';
                for (const part of parts) {
                    if (part) {
                        currentPath += (currentPath ? '/' : '') + part;
                        html += '<span>/</span>';
                        html += `<a href="#" onclick="navigateToFolder('${currentPath}'); return false;">${part}</a>`;
                    }
                }
            }
            
            breadcrumb.innerHTML = html;
        }
        
        function updateFolderView(data) {
            if (!data) {
                navigateToFolder(currentPath);
                return;
            }
            
            document.getElementById('currentFolderName').textContent = data.name || 'Root';
            
            // Update folder stats
            const statsHtml = `
                <div class="stat-card">
                    <div class="label">Direct Size</div>
                    <div class="value">${formatSize(data.total_size)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Recursive Size</div>
                    <div class="value">${formatSize(data.recursive_size || 0)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Direct Files</div>
                    <div class="value">${data.file_count}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Recursive Files</div>
                    <div class="value">${data.recursive_file_count || 0}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Direct Lines</div>
                    <div class="value">${formatNumber(data.total_lines)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Recursive Lines</div>
                    <div class="value">${formatNumber(data.recursive_lines || 0)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Direct Tokens</div>
                    <div class="value">${formatNumber(data.total_tokens)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Recursive Tokens</div>
                    <div class="value">${formatNumber(data.recursive_tokens || 0)}</div>
                </div>
                <div class="stat-card">
                    <div class="label">Depth</div>
                    <div class="value">${data.depth || 0}</div>
                </div>
            `;
            document.getElementById('folderStats').innerHTML = statsHtml;
            
            // Update file table
            const tbody = document.getElementById('fileTableBody');
            let tableHtml = '';
            
            // Add subfolders with metrics
            for (const subfolder of data.subfolders || []) {
                const folderPath = data.path === '.' ? subfolder : `${data.path}/${subfolder}`;
                const isExcluded = excludedPaths.has(folderPath);
                
                // Try to get folder metrics from tree data
                let folderMetrics = null;
                if (treeData) {
                    folderMetrics = findFolderInTree(treeData, folderPath);
                }
                
                tableHtml += `
                    <tr class="${isExcluded ? 'excluded-item' : ''}">
                        <td onclick="navigateToFolder('${folderPath}')" style="cursor: pointer;">
                            📁 <strong class="folder-name">${subfolder}</strong>
                        </td>
                        <td>Folder</td>
                        <td>${folderMetrics ? formatSize((folderMetrics.total_size || 0) + (folderMetrics.recursive_size || 0)) : '-'}</td>
                        <td>${folderMetrics ? formatNumber((folderMetrics.total_lines || 0) + (folderMetrics.recursive_lines || 0)) : '-'}</td>
                        <td>${folderMetrics ? formatNumber((folderMetrics.total_tokens || 0) + (folderMetrics.recursive_tokens || 0)) : '-'}</td>
                        <td>
                            <input type="checkbox" ${!isExcluded ? 'checked' : ''} 
                                   onchange="togglePathSelection('${folderPath}', this.checked)">
                        </td>
                    </tr>
                `;
            }
            
            // Add files
            for (const file of data.files || []) {
                const isExcluded = excludedPaths.has(file.path);
                const willHaveContent = file.will_have_content;
                
                // Determine status badge and styling
                let statusBadge = '';
                let additionalClass = '';
                
                if (file.content_status === 'omitted_large') {
                    statusBadge = '<span class="skip-badge" title="File too large (>256KB) - included in XML but without content">Large</span>';
                    additionalClass = ' no-content-file';
                } else if (file.content_status === 'lock_file_excluded') {
                    statusBadge = '<span class="skip-badge" title="Lock file - included in XML but without content">Lock File</span>';
                    additionalClass = ' no-content-file';
                } else if (file.content_status === 'mock_file_excluded') {
                    statusBadge = '<span class="skip-badge" title="Mock file - included in XML but without content">Mock</span>';
                    additionalClass = ' no-content-file';
                } else if (file.is_binary || file.content_status === 'binary') {
                    statusBadge = '<span class="binary-badge">Binary</span>';
                    additionalClass = ' no-content-file';
                } else if (!willHaveContent) {
                    statusBadge = '<span class="skip-badge" title="Included in XML but without content">No Content</span>';
                    additionalClass = ' no-content-file';
                } else {
                    statusBadge = '<span class="text-badge">Text</span>';
                }
                
                tableHtml += `
                    <tr class="${isExcluded ? 'excluded-item' : ''}${additionalClass}">
                        <td><span class="file-name">📄 ${file.name}</span></td>
                        <td>${statusBadge}</td>
                        <td>${formatSize(file.size)}</td>
                        <td>${!willHaveContent ? '-' : formatNumber(file.lines)}</td>
                        <td>${!willHaveContent ? '-' : formatNumber(file.estimated_tokens)}</td>
                        <td>
                            <input type="checkbox" ${!isExcluded ? 'checked' : ''} 
                                   onchange="togglePathSelection('${file.path}', this.checked)">
                        </td>
                    </tr>
                `;
            }
            
            tbody.innerHTML = tableHtml;
        }
        
        function togglePathSelection(path, selected) {
            if (selected) {
                excludedPaths.delete(path);
                removeChildrenFromExcluded(path);
            } else {
                excludedPaths.add(path);
                addChildrenToExcluded(path);
            }
            
            // Update visual state immediately
            updateItemVisualState(path, !selected);
            renderTree();
            updateStats();
        }
        
        function updateItemVisualState(path, isExcluded) {
            // Update main screen table row
            const tableRows = document.querySelectorAll('#fileTableBody tr');
            tableRows.forEach(row => {
                const checkbox = row.querySelector('input[type="checkbox"]');
                if (checkbox && checkbox.getAttribute('onchange').includes(path)) {
                    if (isExcluded) {
                        row.classList.add('excluded-item');
                        checkbox.checked = false;
                    } else {
                        row.classList.remove('excluded-item');
                        checkbox.checked = true;
                    }
                }
            });
            
            // Update tree node
            const treeNode = document.querySelector(`[data-path="${path}"]`);
            if (treeNode) {
                if (isExcluded) {
                    treeNode.classList.add('excluded');
                } else {
                    treeNode.classList.remove('excluded');
                }
                
                const treeCheckbox = treeNode.querySelector('input[type="checkbox"]');
                if (treeCheckbox) {
                    treeCheckbox.checked = !isExcluded;
                }
            }
        }
        
        function updateStats() {
            let totalSize = 0;
            let totalLines = 0;
            let totalTokens = 0;

            // Treat excluded paths as prefixes (so excluding a folder excludes its descendants)
            const excluded = Array.from(excludedPaths || []);
            const isExcluded = (p) => {
                if (!p) return false;
                for (const ex of excluded) {
                    if (!ex) continue;
                    if (p === ex) return true;
                    if (p.startsWith(ex.endsWith('/') ? ex : ex + '/')) return true;
                }
                return false;
            };

            function calculateStats(node) {
                if (isExcluded(node.path)) return;

                if (node.type === 'file') {
                    // All included files contribute to size; lines/tokens only if content is included
                    totalSize += Number(node.size || 0);
                    if (node.will_have_content && node.is_text !== false) {
                        totalLines += Number(node.lines || 0);
                        totalTokens += Number(node.tokens || 0);
                    }
                }
                if (node.children) {
                    for (const child of node.children) calculateStats(child);
                }
            }

            if (treeData) calculateStats(treeData);

            document.getElementById('totalSize').textContent = formatSize(totalSize);
            document.getElementById('totalLines').textContent = formatNumber(totalLines);
            document.getElementById('totalTokens').textContent = formatNumber(totalTokens);
        }
        
        async function saveConfig() {
            showLoading();
            try {
                const response = await fetch('/api/save-config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({excluded_paths: Array.from(excludedPaths)})
                });
                const result = await response.json();
                if (result.success) {
                    alert('Configuration saved successfully!');
                } else {
                    alert('Error saving configuration: ' + result.error);
                }
            } catch (error) {
                alert('Error saving configuration: ' + error.message);
            } finally {
                hideLoading();
            }
        }
        
        async function loadConfig() {
            showLoading();
            try {
                const response = await fetch('/api/load-config');
                const result = await response.json();
                if (result.success) {
                    excludedPaths = new Set(result.excluded_paths);
                    renderTree();
                    updateStats();
                    updateFolderView();
                    alert('Configuration loaded successfully!');
                } else {
                    alert('No saved configuration found.');
                }
            } catch (error) {
                alert('Error loading configuration: ' + error.message);
            } finally {
                hideLoading();
            }
        }
        
        async function generateXML() {
            if (!confirm('Generate XML with current selection?')) return;
            
            showLoading();
            document.getElementById('generateBtn').disabled = true;
            
            try {
                const response = await fetch('/api/generate-xml', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({excluded_paths: Array.from(excludedPaths)})
                });
                const result = await response.json();
                if (result.success) {
                    alert(`XML generated successfully!\\nFile: ${result.output_file}\\nTotal files: ${result.stats.total_files}\\nTotal size: ${formatSize(result.stats.total_size)}`);
                } else {
                    alert('Error generating XML: ' + result.error);
                }
            } catch (error) {
                alert('Error generating XML: ' + error.message);
            } finally {
                hideLoading();
                document.getElementById('generateBtn').disabled = false;
            }
        }
        
        // Initialize
        window.onload = async () => {
            await loadTree();
            // Automatically load saved configuration if it exists
            await loadConfigSilently();
        };
        
        async function loadConfigSilently() {
            try {
                const response = await fetch('/api/load-config');
                const result = await response.json();
                if (result.success) {
                    excludedPaths = new Set(result.excluded_paths);
                    renderTree();
                    updateStats();
                    // Refresh current folder view to show updated exclusion states
                    navigateToFolder(currentPath || '');
                }
            } catch (error) {
                // Silent failure - no alert on page load
                console.warn('Could not load saved configuration:', error);
            }
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/tree')
def get_tree():
    tree = analyzer.get_tree_structure()
    return jsonify(tree)

@app.route('/api/folder')
def get_folder():
    path = request.args.get('path', '')
    try:
        folder_info = analyzer.analyze_folder(path)
        return jsonify(asdict(folder_info))
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/save-config', methods=['POST'])
def save_config():
    try:
        data = request.json
        analyzer.excluded_paths = set(data.get('excluded_paths', []))
        analyzer.save_configuration()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/load-config')
def load_config():
    if analyzer.load_configuration():
        return jsonify({
            'success': True,
            'excluded_paths': list(analyzer.excluded_paths)
        })
    return jsonify({'success': False})

@app.route('/api/generate-xml', methods=['POST'])
def generate_xml():
    try:
        data = request.json
        analyzer.excluded_paths = set(data.get('excluded_paths', []))
        
        # Check if codebase_to_xml.py exists
        if not (analyzer.root_path / 'codebase_to_xml.py').exists():
            # If not, create a simple XML generator
            output_file = analyzer.root_path / 'codebase.xml'
            stats = generate_simple_xml(analyzer, output_file)
        else:
            output_file = analyzer.generate_xml()
            stats = calculate_xml_stats(output_file)
        
        return jsonify({
            'success': True,
            'output_file': str(output_file),
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def generate_simple_xml(analyzer, output_file):
    """Enhanced XML generator with file tree and proper structure"""
    total_files = 0
    total_size = 0
    total_characters = 0
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f'<codebase root="{escape_xml_attr(analyzer.root_path.name)}">\n')
        
        # Add metadata section
        f.write('  <metadata>\n')
        f.write(f'    <root_path>{escape_xml_attr(str(analyzer.root_path))}</root_path>\n')
        f.write('  </metadata>\n')
        
        # Add summary section with file tree
        f.write('  <summary>\n')
        tree_content = generate_tree_summary_string(
            analyzer.root_path, 
            analyzer.excluded_paths, 
            analyzer.gitignore_patterns, 
            analyzer.custom_excludes
        )
        f.write(f'    {write_cdata(tree_content)}\n')
        f.write('  </summary>\n')
        
        # Add structure section
        f.write('  <structure>\n')
        
        def write_file_element(file_path: Path, indent: str):
            nonlocal total_files, total_size, total_characters
            
            relative_path_str = str(file_path.relative_to(analyzer.root_path)).replace("\\", "/")
            file_info = analyzer._analyze_file(file_path)
            
            if not file_info:
                return
            
            try:
                file_stat = file_path.stat()
                attrs = {
                    "path": relative_path_str,
                    "language": get_file_language(file_path),
                    "size": str(file_info.size),
                    "lines": str(file_info.lines),
                    "last_modified": str(int(file_stat.st_mtime))
                }
                
                content = None
                status = "ok"
                
                # Check file constraints
                if file_info.size > 1024 * 256:  # 256KB limit
                    status = "omitted_large"
                elif file_info.is_binary:
                    status = "binary"
                else:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file:
                            content = file.read()
                            if not content.strip():
                                status = "empty"
                    except Exception:
                        status = "read_error"
                
                if status != "ok":
                    attrs["status"] = status
                
                attr_str = " ".join([f'{key}="{escape_xml_attr(str(value))}"' for key, value in attrs.items()])
                
                if content and status == "ok":
                    f.write(f'{indent}<file {attr_str}>\n')
                    f.write(f"{indent}  {write_cdata(content)}\n")
                    f.write(f'{indent}</file>\n')
                    total_characters += len(content)
                else:
                    f.write(f'{indent}<file {attr_str} />\n')
                
                total_files += 1
                total_size += file_info.size
                
            except OSError:
                f.write(f'{indent}<file path="{escape_xml_attr(relative_path_str)}" status="access_error" />\n')
        
        def walk_directory(current_path: Path, indent: str = "    "):
            try:
                entries = sorted([p for p in current_path.iterdir()], key=lambda p: (p.is_file(), p.name.lower()))
            except (PermissionError, FileNotFoundError):
                return
            
            for entry in entries:
                rel_path = str(entry.relative_to(analyzer.root_path))
                
                # Skip excluded paths
                if rel_path in analyzer.excluded_paths or analyzer._should_exclude(entry):
                    continue
                
                if entry.is_dir():
                    f.write(f'{indent}<directory name="{escape_xml_attr(entry.name)}">\n')
                    walk_directory(entry, indent + "  ")
                    f.write(f'{indent}</directory>\n')
                else:
                    write_file_element(entry, indent)
        
        walk_directory(analyzer.root_path)
        
        f.write('  </structure>\n')
        f.write('</codebase>\n')
    
    return {
        'total_files': total_files, 
        'total_size': total_size, 
        'total_characters': total_characters,
        'estimated_tokens': int(total_characters * TOKENS_PER_CHAR_ESTIMATE)
    }

def calculate_xml_stats(xml_file):
    """Calculate statistics from generated XML"""
    stats = {'total_files': 0, 'total_size': 0}
    
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_file)
        root = tree.getroot()
        
        for file_elem in root.findall('.//file'):
            stats['total_files'] += 1
            size = file_elem.get('size')
            if size:
                stats['total_size'] += int(size)
    except:
        pass
    
    return stats

def main():
    global analyzer
    
    import argparse
    parser = argparse.ArgumentParser(description='Web interface for codebase analysis and XML generation')
    parser.add_argument('path', nargs='?', default='.', help='Path to analyze (default: current directory)')
    parser.add_argument('--port', type=int, default=5000, help='Port to run server on (default: 5000)')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    
    args = parser.parse_args()
    
    root_path = Path(args.path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        print(f"Error: {args.path} is not a valid directory")
        sys.exit(1)
    
    print(f"Analyzing codebase at: {root_path}")
    analyzer = CodebaseAnalyzer(str(root_path))
    
    # Try to load existing configuration
    if analyzer.load_configuration():
        print(f"Loaded existing configuration from {CONFIG_FILE}")
    
    print(f"Starting web server at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop the server")
    
    try:
        app.run(host=args.host, port=args.port, debug=False)
    except KeyboardInterrupt:
        print("\nServer stopped")

if __name__ == '__main__':
    main()
