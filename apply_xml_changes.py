"""
XML Code Modification Applier - Applies AI-generated code modifications from XML
This script processes XML modifications and applies them to the codebase safely.
It is designed to be robust, continuing even if one modification fails.
"""

import argparse
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
import re
import difflib
from typing import Tuple, Optional
import shutil
from datetime import datetime

class CodeModificationError(Exception):
    """Custom exception for code modification errors."""
    pass

class XMLCodeApplier:
    """Handles application of XML-based code modifications."""
    
    def __init__(self, root_path: Path, backup_dir: Optional[Path] = None, 
                 dry_run: bool = False, similarity_threshold: float = 0.95):
        self.root_path = root_path.resolve()
        self.backup_dir = backup_dir
        self.dry_run = dry_run
        self.similarity_threshold = similarity_threshold
        self.applied_modifications = []
        self.failed_modifications = []
        
        if self.backup_dir and not self.dry_run:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            logging.info(f"Backup directory: {self.backup_dir}")
   
    def extract_code_from_cdata(self, content: str) -> str:
        """Extracts code from a CDATA block, which includes a markdown code block."""
        if not content:
            return ""
            
        content = content.strip()
        
        # Strip the ```language and ``` markers
        lines = content.split('\n')
        if lines and lines[0].strip().startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        
        return '\n'.join(lines)
    
    def normalize_code_for_comparison(self, code: str) -> str:
        """Normalize code for comparison by removing comments and extra whitespace."""
        lines = []
        for line in code.split('\n'):
            line = re.sub(r'#.*$', '', line)
            line = re.sub(r'//.*$', '', line)
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        return '\n'.join(lines)
    
    def calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two text strings."""
        normalized1 = self.normalize_code_for_comparison(text1)
        normalized2 = self.normalize_code_for_comparison(text2)
        matcher = difflib.SequenceMatcher(None, normalized1, normalized2)
        return matcher.ratio()
    
    def backup_file(self, file_path: Path) -> Optional[Path]:
        """Create a backup of the file before modification."""
        if not self.backup_dir or self.dry_run or not file_path.exists():
            return None
        
        relative_path = file_path.relative_to(self.root_path)
        backup_path = self.backup_dir / relative_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.copy2(file_path, backup_path)
            logging.debug(f"Backed up {file_path} to {backup_path}")
            return backup_path
        except Exception as e:
            logging.warning(f"Could not backup {file_path}: {e}")
            return None
    
    def apply_create_file(self, mod: ET.Element):
        """Apply CREATE_FILE modification, raising an exception on failure."""
        path_str = mod.get('path')
        if not path_str: raise CodeModificationError("CREATE_FILE missing path attribute")
        
        file_path = self.root_path / path_str
        if file_path.exists():
            raise CodeModificationError(f"File {path_str} already exists, cannot create.")

        content_cdata = mod.text
        if not content_cdata or not content_cdata.strip():
            raise CodeModificationError(f"No content found for {path_str}")
        
        content = self.extract_code_from_cdata(content_cdata)
        
        if self.dry_run:
            logging.info(f"[DRY RUN] Would create file: {path_str} with {len(content)} chars")
        else:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
            logging.info(f"Created file: {path_str}")

    def apply_delete_file(self, mod: ET.Element):
        """Apply DELETE_FILE modification, raising an exception on failure."""
        path_str = mod.get('path')
        if not path_str: raise CodeModificationError("DELETE_FILE missing path attribute")
        
        file_path = self.root_path / path_str
        if not file_path.exists():
            raise CodeModificationError(f"File {path_str} does not exist, cannot delete.")
        if not file_path.is_file():
            raise CodeModificationError(f"Path {path_str} is a directory, not a file.")

        reason_elem = mod.find('reason')
        reason = reason_elem.text.strip() if reason_elem is not None else "No reason provided"
        
        if self.dry_run:
            logging.info(f"[DRY RUN] Would delete file: {path_str} (Reason: {reason})")
        else:
            self.backup_file(file_path)
            file_path.unlink()
            logging.info(f"Deleted file: {path_str} (Reason: {reason})")
    
    def apply_replace_file(self, mod: ET.Element):
        """Apply REPLACE_FILE modification, raising an exception on failure."""
        path_str = mod.get('path')
        if not path_str: raise CodeModificationError("REPLACE_FILE missing path attribute")

        file_path = self.root_path / path_str
        if not file_path.exists():
            raise CodeModificationError(f"File {path_str} does not exist, cannot replace.")
        
        new_content_cdata = mod.text
        if not new_content_cdata or not new_content_cdata.strip():
            raise CodeModificationError(f"No new content found for {path_str}")
        
        new_content = self.extract_code_from_cdata(new_content_cdata)
        existing_content = file_path.read_text(encoding='utf-8')
        
        existing_lines = len(existing_content.splitlines())
        new_lines = len(new_content.splitlines())
        if existing_lines > 50 and (existing_lines - new_lines) > 50:
             raise CodeModificationError(f"Significant size reduction blocked ({existing_lines} -> {new_lines} lines)")

        reason_elem = mod.find('reason')
        reason = reason_elem.text.strip() if reason_elem is not None else "No reason provided"
        
        if self.dry_run:
            logging.info(f"[DRY RUN] Would replace file: {path_str} ({existing_lines} -> {new_lines} lines)")
        else:
            self.backup_file(file_path)
            file_path.write_text(new_content, encoding='utf-8')
            logging.info(f"Replaced file: {path_str} ({existing_lines} -> {new_lines} lines)")
        logging.debug(f"Reason: {reason}")
    
    def apply_replace_section(self, mod: ET.Element):
        """Apply REPLACE_SECTION modification, raising an exception on failure."""
        path_str = mod.get('path')
        if not path_str: raise CodeModificationError("REPLACE_SECTION missing path attribute")

        file_path = self.root_path / path_str
        if not file_path.exists():
            raise CodeModificationError(f"File {path_str} does not exist, cannot replace section.")

        old_content_elem = mod.find('old_content')
        new_content_elem = mod.find('new_content')
        if old_content_elem is None or not old_content_elem.text: raise CodeModificationError("No old_content found")
        if new_content_elem is None or not new_content_elem.text: raise CodeModificationError("No new_content found")

        old_code = self.extract_code_from_cdata(old_content_elem.text)
        new_code = self.extract_code_from_cdata(new_content_elem.text)
        
        existing_content = file_path.read_text(encoding='utf-8')
        
        # A simple string replace is often faster and good enough if the match is exact.
        if old_code in existing_content:
             if self.dry_run:
                logging.info(f"[DRY RUN] Would replace section in: {path_str} via direct match")
             else:
                self.backup_file(file_path)
                new_file_content = existing_content.replace(old_code, new_code, 1)
                file_path.write_text(new_file_content, encoding='utf-8')
                logging.info(f"Replaced section in: {path_str} (exact match)")
                return # Exit successfully after exact match

        # Fallback to fuzzy matching if exact match fails
        old_lines_list = old_code.splitlines()
        existing_lines_list = existing_content.splitlines()
        best_match_start, best_similarity = -1, 0

        for i in range(len(existing_lines_list) - len(old_lines_list) + 1):
            section = '\n'.join(existing_lines_list[i : i + len(old_lines_list)])
            similarity = self.calculate_similarity(old_code, section)
            if similarity > best_similarity:
                best_similarity, best_match_start = similarity, i
        
        if best_similarity < self.similarity_threshold:
            raise CodeModificationError(f"No matching section found (best similarity: {best_similarity:.2%})")

        if self.dry_run:
            logging.info(f"[DRY RUN] Would replace section in {path_str} at lines {best_match_start + 1}-{best_match_start + len(old_lines_list)} (similarity: {best_similarity:.2%})")
        else:
            self.backup_file(file_path)
            new_file_lines = (existing_lines_list[:best_match_start] +
                                new_code.splitlines() +
                                existing_lines_list[best_match_start + len(old_lines_list):])
            file_path.write_text('\n'.join(new_file_lines), encoding='utf-8')
            logging.info(f"Replaced section in {path_str} (fuzzy match at lines {best_match_start+1}-..., similarity: {best_similarity:.2%})")

    def apply_modifications_from_xml(self, xml_file: Path) -> Tuple[int, int]:
        """Apply all modifications from XML file robustly."""
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()
        except ET.ParseError as e:
            raise CodeModificationError(f"Invalid XML format in {xml_file.name}: {e}")

        if root.tag != 'modifications':
            raise CodeModificationError(f"Expected root element 'modifications', got '{root.tag}'")
            
        modifications = root.findall('modification')
        logging.info(f"Found {len(modifications)} modification(s) to apply.")
            
        for i, mod in enumerate(modifications, 1):
            mod_type = mod.get('type')
            path = mod.get('path', 'unknown')
            logging.info(f"--- Applying modification {i}/{len(modifications)}: {mod_type} on {path} ---")
            
            try:
                if mod_type == 'CREATE_FILE': self.apply_create_file(mod)
                elif mod_type == 'DELETE_FILE': self.apply_delete_file(mod)
                elif mod_type == 'REPLACE_FILE': self.apply_replace_file(mod)
                elif mod_type == 'REPLACE_SECTION': self.apply_replace_section(mod)
                else: raise CodeModificationError(f"Unknown modification type: {mod_type}")
                
                # If no exception was raised, the modification was successful.
                self.applied_modifications.append({'type': mod_type, 'path': path, 'status': 'success'})

            except Exception as e:
                # Catch any exception from the apply_* methods.
                logging.error(f"Failed to apply modification {i} ({mod_type} on {path}): {e}")
                self.failed_modifications.append({'type': mod_type, 'path': path, 'error': str(e)})

        return len(self.applied_modifications), len(self.failed_modifications)

    def generate_report(self) -> str:
        """Generate a summary report of applied modifications."""
        report = ["="*60, "CODE MODIFICATION REPORT", "="*60,
                  f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                  f"Root Path: {self.root_path}", f"Dry Run: {'Yes' if self.dry_run else 'No'}", ""]
        if self.applied_modifications:
            report.append(f"SUCCESSFUL MODIFICATIONS ({len(self.applied_modifications)}):")
            for mod in self.applied_modifications: report.append(f"✓ {mod['type']}: {mod['path']}")
            report.append("")
        if self.failed_modifications:
            report.append(f"FAILED MODIFICATIONS ({len(self.failed_modifications)}):")
            for mod in self.failed_modifications: report.append(f"✗ {mod['type']}: {mod['path']}\n  Error: {mod['error']}")
            report.append("")
        report.append(f"Summary: {len(self.applied_modifications)} successful, {len(self.failed_modifications)} failed")
        report.append("="*60)
        return '\n'.join(report)

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Apply XML-based code modifications to a codebase", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xml_file", type=str, help="Path to XML file containing modifications")
    parser.add_argument("--root-path", type=str, default=".", help="Root path of the codebase. Default: current directory")
    parser.add_argument("--backup-dir", type=str, help="Directory to store backups of modified files")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--similarity-threshold", type=float, default=0.95, help="Similarity threshold for section matching (0.0-1.0). Default: 0.95")
    parser.add_argument("--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level. Default: INFO")
    parser.add_argument("--report-file", type=str, help="Save detailed report to file")
    return parser.parse_args()

def setup_logging(log_level: str):
    """Configure logging for the script."""
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format='%(levelname)s: %(message)s', stream=sys.stdout)

def main():
    """Main function."""
    args = parse_arguments()
    setup_logging(args.log_level)
    
    xml_file = Path(args.xml_file)
    if not xml_file.exists(): logging.error(f"XML file not found: {xml_file}"); sys.exit(1)
    root_path = Path(args.root_path)
    if not root_path.is_dir(): logging.error(f"Root path is not a directory: {root_path}"); sys.exit(1)
    backup_dir = Path(args.backup_dir) if args.backup_dir else None
    
    logging.info(f"Starting code modification process from '{xml_file.name}'...")
    
    try:
        applier = XMLCodeApplier(root_path=root_path, backup_dir=backup_dir, dry_run=args.dry_run, similarity_threshold=args.similarity_threshold)
        success_count, failure_count = applier.apply_modifications_from_xml(xml_file)
        report = applier.generate_report()
        print("\n" + report)
        
        if args.report_file:
            Path(args.report_file).write_text(report, encoding='utf-8')
            logging.info(f"Report saved to: {args.report_file}")
        
        if failure_count > 0:
            logging.warning(f"Process completed with {failure_count} failure(s).")
            sys.exit(1)
        else:
            logging.info("All modifications applied successfully.")
            sys.exit(0)
            
    except CodeModificationError as e:
        logging.critical(f"A critical error occurred: {e}")
        sys.exit(1)
    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
