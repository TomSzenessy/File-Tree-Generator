import argparse
import logging
from pathlib import Path
import sys
import fnmatch
import json 

# --- Configuration ---
# This section defines global constants and settings used throughout the script.

# Character sets for drawing the file tree structure in the output.
# PRETTY_TREE_CHARS uses more visually appealing Unicode characters for a human-readable tree.
PRETTY_TREE_CHARS = {"space": "    ", "branch": "│   ", "tee": "├── ", "corner": "└── ", "dir": "📁", "file": "📄"}
# COMPACT_TREE_CHARS uses simpler ASCII characters, which can be useful for token optimization in certain contexts.
COMPACT_TREE_CHARS = {"space": "  ", "branch": " | ", "tee": " + ", "corner": " L ", "dir": "d", "file": "f"}

# Default list of directory and file names to exclude from the file tree generation.
# These are common project-related folders/files that are usually not relevant for a file tree.
DEFAULT_EXCLUDES = ['.git', '.vscode', 'node_modules', '__pycache__', 'dist', 'build', '.DS_Store', 'coverage', '.next', 'out', 'logs', '.env', 'file_tree.txt']
# A set of file extensions that are typically binary files (images, fonts, archives, databases).
# Content from these files will be omitted to prevent unreadable characters in the output.
BINARY_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.eot', '.ttf', '.woff', '.woff2', '.otf', '.zip', '.gz', '.db'}

# List of common lock files (e.g., from npm, yarn, pnpm).
# The content of these files will be specifically omitted or summarized for brevity when smart truncation is enabled.
LOCK_FILES = ['package-lock.json', 'yarn.lock', 'pnpm-lock.yaml']

def setup_logging(log_level):
    """
    Configures the logging settings for the script.

    This function sets up the basic logging configuration, including the log level
    (e.g., DEBUG, INFO, WARNING, ERROR) and the format of the log messages.
    Log messages are printed to standard output (console).

    Args:
        log_level (str): The desired logging level as a string (e.g., "INFO", "DEBUG").
                         If an invalid level is provided, it defaults to INFO.
    """
    # Convert the string log level to its corresponding numeric value from the logging module.
    numeric_level = getattr(logging, log_level.upper(), None)
    # If the provided log_level string does not match a valid logging level, inform the user and default to INFO.
    if not isinstance(numeric_level, int):
        print(f"Invalid log level: {log_level}. Defaulting to INFO.")
        numeric_level = logging.INFO
    # Configure the basic logging system.
    # level: Sets the threshold for the logger. Messages below this level will be ignored.
    # format: Defines the layout of log records.
    # stream: Specifies the output stream for log messages (sys.stdout means console).
    logging.basicConfig(level=numeric_level, format='%(levelname)s: %(message)s', stream=sys.stdout)

def parse_arguments():
    """
    Parses command-line arguments provided to the script.

    This function defines all the available command-line arguments, their types,
    default values, and help messages. It uses `argparse` to create a robust
    command-line interface for the `tree_gen.py` script.

    Returns:
        argparse.Namespace: An object containing the parsed arguments as attributes.
    """
    parser = argparse.ArgumentParser(
        description="Generate a file tree with smart content truncation, respecting .gitignore.",
        epilog="""
Examples:
  %(prog)s . 4 --output tree.txt
    Generate a smart-truncated tree of the current directory up to depth 4, saving to 'tree.txt'.
    (Smart truncation is the default behavior unless --no-truncate is used.)
    
  %(prog)s . 3 --pretty
    Generate a tree of the current directory up to depth 3, using pretty (human-readable) characters.
    (By default, compact characters are used for token optimization.)
    
  %(prog)s . 2 --no-truncate
    Generate a summary tree, then a detailed tree with full file content (up to max-file-size)
    for the current directory up to depth 2. This is the default behavior when neither
    --only-summary nor --only-content is specified.

  %(prog)s . 5
    Generate a tree of the current directory up to depth 5, omitting file sizes (default behavior).
    To include file sizes, use --include-file-sizes.
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter # Allows for custom formatting of the help message, including newlines in epilog.
    )
    # Positional argument: folder_path
    parser.add_argument("folder_path", type=str, nargs='?', default=".",
                        help="Path to the root folder from which to generate the tree. Defaults to the current directory ('.').")
    # Positional argument: depth
    parser.add_argument("depth", type=int, nargs='?', default=6,
                        help="Maximum depth of subdirectories to traverse. Files/directories beyond this depth will not be included. Defaults to 6.")

    # Optional argument: --pretty
    parser.add_argument("--pretty", action="store_true",
                        help="Use a pretty, human-readable character set (e.g., '├── ', '│   ') for the tree structure. By default, a compact character set (' + ', ' | ') is used for better token optimization.")
    # Optional argument: --include-file-sizes
    parser.add_argument("--include-file-sizes", action="store_true",
                        help="Include the size of each file in the output (e.g., 'file.txt [1.2 KB]'). By default, file sizes are omitted.")

    # Mutually exclusive group for content display options.
    # Only one of these arguments can be used at a time.
    content_display_group = parser.add_mutually_exclusive_group()
    # Optional argument: --only-summary
    content_display_group.add_argument("--only-summary", action="store_true",
                                       help="Only generate the summary file tree (directory and file names), without including any file contents.")
    # Optional argument: --only-content
    content_display_group.add_argument("--only-content", action="store_true",
                                       help="Only generate the detailed file tree that includes file contents, without the initial summary view.")
    # Optional argument: --no-truncate
    parser.add_argument("--no-truncate", action="store_true",
                        help="Do not truncate file content. Include the full content of files up to the `max-file-size` limit. By default, smart truncation is applied.")

    # Optional argument: --truncate-limit
    parser.add_argument("--truncate-limit", type=int, default=500,
                        help="When smart truncation is enabled, this specifies the number of lines to show from the head and tail of a file. Default: 500 lines (500 from start, 500 from end).")
    # Optional argument: --max-file-size
    parser.add_argument("--max-file-size", type=int, default=1024 * 512, # 512 KB
                        help="Maximum file size (in bytes) for content inclusion. Files larger than this limit will have their content omitted, regardless of truncation settings. Default: 512KB.")
    # Optional argument: --output
    parser.add_argument("--output", type=str, default="file_tree.txt",
                        help="The name of the output file where the generated file tree will be saved. Default: 'file_tree.txt'.")
    # Optional argument: --exclude
    parser.add_argument("--exclude", type=str, nargs='*', default=[],
                        help="Additional file or directory names to exclude from the tree. These are added to the `DEFAULT_EXCLUDES` list.")
    # Optional argument: --no-gitignore
    parser.add_argument("--no-gitignore", action="store_true", help="Ignore any `.gitignore` files found in the directory structure. By default, `.gitignore` rules are respected.")
    # Optional argument: --log-level
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Sets the logging verbosity level for the script. Default: INFO.")

    return parser.parse_args()

def get_display_size(size_bytes):
    """
    Converts a file size in bytes into a human-readable format (e.g., KB, MB, GB).

    Args:
        size_bytes (int): The size of the file in bytes.

    Returns:
        str: A formatted string representing the file size (e.g., "100 B", "1.5 KB", "2.3 MB").
    """
    # If the size is less than 1KB, display in bytes.
    if size_bytes < 1024: return f"{size_bytes} B"
    # Iterate through units (KB, MB, GB, TB) to find the most appropriate one.
    for unit in ['KB', 'MB', 'GB', 'TB']:
        # If the size is less than the next unit (1024 of current unit), format and return.
        if size_bytes < 1024: return f"{size_bytes:.1f} {unit}"
        # Otherwise, convert to the next larger unit.
        size_bytes /= 1024
    # If the size is extremely large, return in Petabytes.
    return f"{size_bytes:.1f} PB"

def parse_gitignore(gitignore_path):
    """
    Parses a `.gitignore` file and extracts the patterns to be ignored.

    Args:
        gitignore_path (Path): The `pathlib.Path` object pointing to the `.gitignore` file.

    Returns:
        list: A list of strings, where each string is an exclusion pattern from the `.gitignore` file.
              Returns an empty list if the file does not exist or is empty.
    """
    # Check if the .gitignore file actually exists.
    if not gitignore_path.is_file(): return []
    patterns = []
    # Open the .gitignore file for reading with UTF-8 encoding.
    with open(gitignore_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip() # Remove leading/trailing whitespace.
            # Ignore empty lines and lines starting with '#' (comments).
            if line and not line.startswith('#'):
                patterns.append(line)
    return patterns

def should_exclude(path, base_exclude, gitignore_patterns):
    """
    Determines if a given file or directory path should be excluded from the tree.

    Exclusion is based on:
    1.  `DEFAULT_EXCLUDES` and user-provided `--exclude` patterns.
    2.  Patterns found in `.gitignore` files relevant to the current path.

    Args:
        path (Path): The `pathlib.Path` object of the file or directory to check.
        base_exclude (list): A combined list of default and user-specified exclusion patterns.
        gitignore_patterns (list): A list of tuples, where each tuple contains a `.gitignore` pattern
                                   and the `pathlib.Path` of the `.gitignore` file it originated from.
                                   This allows for context-aware matching.

    Returns:
        bool: True if the path should be excluded, False otherwise.
    """
    path_name = path.name # Get just the name of the file or directory (e.g., 'my_folder', 'index.js').

    # 1. Check against default and user-provided explicit excludes.
    if path_name in base_exclude:
        logging.debug(f"Excluded by default/user pattern: {path_name}")
        return True

    # 2. Check against .gitignore patterns.
    # Iterate through gitignore patterns in reverse order to prioritize patterns from closer .gitignore files.
    for pattern, gitignore_dir in reversed(gitignore_patterns):
        try:
            # Calculate the path relative to the .gitignore file's directory.
            rel_path = path.relative_to(gitignore_dir)
            
            # Handle directory patterns that end with a '/' (e.g., 'build/').
            if pattern.endswith('/'):
                # For directory patterns, match against the directory's relative path or its name.
                # `fnmatch.fnmatch` performs shell-style wildcard matching.
                if path.is_dir() and (fnmatch.fnmatch(str(rel_path), pattern[:-1]) or fnmatch.fnmatch(path_name, pattern[:-1])):
                    logging.debug(f"Excluded by gitignore directory pattern '{pattern}' from {gitignore_dir}: {path}")
                    return True
            else:
                # For file patterns or general patterns (e.g., '*.log', 'temp*').
                # Match against the relative path or just the file/directory name.
                if fnmatch.fnmatch(str(rel_path), pattern) or fnmatch.fnmatch(path_name, pattern):
                    logging.debug(f"Excluded by gitignore pattern '{pattern}' from {gitignore_dir}: {path}")
                    return True
        except ValueError:
            # This exception occurs if `path` is not a child of `gitignore_dir`,
            # meaning the current .gitignore pattern does not apply to this path.
            continue
    return False # If no exclusion rule matches, the path is not excluded.

def truncate_content(content, limit, entry):
    """
    Truncates file content to show only the first and last `limit` number of lines.
    This is used for "smart truncation" to keep the output concise.

    Args:
        content (str): The full content of the file.
        limit (int): The number of lines to show from the beginning and end of the file.
        entry (Path): The `pathlib.Path` object of the file (used for logging/printing its name).

    Returns:
        str: The truncated content string, with an ellipsis marker in the middle if truncation occurred.
    """
    lines = content.splitlines() # Split the content into individual lines.
    # If the total number of lines is less than or equal to twice the limit, no truncation is needed.
    if len(lines) <= limit * 2:
        return content
    # Get the first 'limit' lines.
    head = "\n".join(lines[:limit])
    # Get the last 'limit' lines.
    tail = "\n".join(lines[-limit:])
    # Print a message indicating how many lines were truncated.
    print(f"Truncating {len(lines) - (limit * 2)} lines from file {entry.name}")
    # Return the combined head, truncation marker, and tail.
    return f"{head}\n\n... [content truncated] ...\n\n{tail}"

def summarize_package_json(content):
    """
    Summarizes the content of a `package.json` file to extract key information.
    This is a special case for smart truncation to provide relevant project details.

    Args:
        content (str): The full JSON content of the `package.json` file.

    Returns:
        str: A JSON string containing a summary of the `package.json` (name, version, scripts, dependencies, devDependencies).
             Returns "[Could not parse package.json]" if the content is not valid JSON.
    """
    try:
        data = json.loads(content) # Parse the JSON content into a Python dictionary.
        # Extract specific keys if they exist in the parsed data.
        summary = {k: data.get(k) for k in ["name", "version", "scripts", "dependencies", "devDependencies"] if data.get(k)}
        return json.dumps(summary, indent=2) # Return the summary as a pretty-printed JSON string.
    except json.JSONDecodeError:
        # Handle cases where the file content is not valid JSON.
        return "[Could not parse package.json]"

def create_file_tree(root_path, output_file, depth, exclude_patterns, no_gitignore,
                     pretty_chars, omit_file_sizes, show_content, use_smart_truncate,
                     truncate_limit, max_file_size, is_summary_run=False, is_first_output_run=True):
    """
    Recursively generates the file tree structure and writes it to the specified output file.

    This is the core function that traverses the directory, applies exclusion rules,
    and formats the output based on the provided arguments. It handles both summary
    and detailed content views.

    Args:
        root_path (str): The starting directory path for generating the tree.
        output_file (str): The path to the file where the tree output will be written.
        depth (int): The maximum recursion depth for directories.
        exclude_patterns (list): Additional patterns to exclude (from command-line).
        no_gitignore (bool): If True, `.gitignore` files will be ignored.
        pretty_chars (bool): If True, use pretty Unicode characters for the tree.
        omit_file_sizes (bool): If True, file sizes will not be included in the output.
        show_content (bool): If True, file contents will be included (subject to truncation/size limits).
        use_smart_truncate (bool): If True, apply smart truncation to file contents.
        truncate_limit (int): Number of lines for head/tail truncation.
        max_file_size (int): Maximum file size for content inclusion.
        is_summary_run (bool): Internal flag, True if this is the summary view generation pass.
        is_first_output_run (bool): Internal flag, True if this is the very first write operation to the output file.
                                    Used to determine if the file should be overwritten ('w') or appended to ('a').
    """
    root = Path(root_path).resolve() # Resolve the root path to its absolute form.
    all_exclude = DEFAULT_EXCLUDES + exclude_patterns # Combine default and user-provided excludes.
    gitignore_cache = {} # Cache for parsed .gitignore files to avoid re-reading them.
    
    # Select the appropriate character set based on the `pretty_chars` argument.
    TREE_CHARS = PRETTY_TREE_CHARS if pretty_chars else COMPACT_TREE_CHARS
    
    # Determine the file mode: 'w' for the first run (overwrite), 'a' for subsequent runs (append).
    file_mode = 'w' if is_first_output_run else 'a'

    # Open the output file.
    with open(output_file, file_mode, encoding='utf-8') as f:
        # Only write the root directory name for the very first output operation.
        if is_first_output_run:
            f.write(f"{TREE_CHARS['dir']} {root.name}/\n")

        def walk_dir(current_path, prefix="", current_depth=0, parent_patterns=None):
            """
            Internal recursive function to traverse directories and build the tree.

            Args:
                current_path (Path): The current directory being processed.
                prefix (str): The string prefix for the current level of the tree (e.g., "│   ").
                current_depth (int): The current depth of recursion.
                parent_patterns (list): Accumulated .gitignore patterns from parent directories.
            """
            # Stop recursion if the maximum depth is reached.
            if current_depth >= depth: return

            # Initialize gitignore patterns for the current directory, inheriting from parents.
            gitignore_patterns = list(parent_patterns) if parent_patterns else []
            # If .gitignore files are not being ignored by the user.
            if not no_gitignore:
                gitignore_file = current_path / '.gitignore' # Path to the .gitignore file in the current directory.
                # Check if this .gitignore file has already been parsed and cached.
                if str(gitignore_file) not in gitignore_cache:
                    # If not cached, parse it and store in cache.
                    gitignore_cache[str(gitignore_file)] = parse_gitignore(gitignore_file)
                # Add new patterns from the current .gitignore file to the accumulated list.
                new_patterns = gitignore_cache[str(gitignore_file)]
                gitignore_patterns.extend([(p, current_path) for p in new_patterns])

            try:
                # Get all entries (files and directories) in the current path.
                # Sort them: directories first, then files, both alphabetically by name (case-insensitive).
                entries = sorted([p for p in current_path.iterdir()], key=lambda p: (p.is_file(), p.name.lower()))
                # Filter out entries that should be excluded based on rules.
                filtered_entries = [e for e in entries if not should_exclude(e, all_exclude, gitignore_patterns)]
            except (PermissionError, FileNotFoundError) as e:
                # Log a warning if the script cannot access a directory.
                logging.warning(f"Could not access {current_path}: {e}")
                return # Stop processing this branch.

            # Iterate through the filtered entries to build the tree output.
            for i, entry in enumerate(filtered_entries):
                is_last = i == len(filtered_entries) - 1 # Check if this is the last entry in the current directory.
                # Choose the appropriate connector character (tee for middle, corner for last).
                connector = TREE_CHARS["corner"] if is_last else TREE_CHARS["tee"]
                
                if entry.is_dir():
                    # If it's a directory, write its name to the file.
                    f.write(f"{prefix}{connector}{TREE_CHARS['dir']} {entry.name}/\n")
                    # Calculate the new prefix for children: branch or space depending on `is_last`.
                    new_prefix = prefix + (TREE_CHARS["space"] if is_last else TREE_CHARS["branch"])
                    # Recursively call walk_dir for the subdirectory.
                    walk_dir(entry, new_prefix, current_depth + 1, gitignore_patterns)
                else: # It's a file
                    try:
                        file_stat = entry.stat() # Get file statistics (like size).
                        # Format file size string if not omitted.
                        size_str = f" [{get_display_size(file_stat.st_size)}]" if not omit_file_sizes else ""
                        # Write the file name and size (if included) to the output.
                        f.write(f"{prefix}{connector}{TREE_CHARS['file']} {entry.name}{size_str}\n")
                        
                        # Calculate the content prefix for indentation.
                        content_prefix = prefix + (TREE_CHARS["space"] if is_last else TREE_CHARS["branch"])
                        
                        # If `show_content` is False (e.g., in summary view), skip reading/writing content.
                        if not show_content: continue

                        # Check if file content should be omitted due to size or binary nature.
                        if file_stat.st_size > max_file_size:
                            f.write(f"{content_prefix}    [File content omitted, size > {get_display_size(max_file_size)}]\n")
                        elif entry.suffix in BINARY_EXTENSIONS:
                            f.write(f"{content_prefix}    [Binary/SVG content omitted]\n")
                        elif use_smart_truncate and entry.name in LOCK_FILES:
                            f.write(f"{content_prefix}    [Lock file content omitted for brevity]\n")
                        else:
                            try:
                                content = entry.read_text('utf-8') # Read the file content as UTF-8 text.
                                content_to_write = content # Initialize content to write.
                                
                                if use_smart_truncate:
                                    # Special handling for package.json to summarize it.
                                    if entry.name == 'package.json':
                                        content_to_write = summarize_package_json(content)
                                        f.write(f"{content_prefix}    ---- Summary ----\n")
                                    else:
                                        # Apply general smart truncation for other text files.
                                        content_to_write = truncate_content(content, truncate_limit, entry)
                                        f.write(f"{content_prefix}    ---- Content ----\n")
                                else: # If smart truncation is disabled, include full content.
                                    f.write(f"{content_prefix}    ---- Content ----\n")

                                # Write each line of the (potentially truncated) content to the output file.
                                for line in content_to_write.splitlines():
                                    f.write(f"{content_prefix}    {line}\n")
                                
                                # Add an end marker for clarity.
                                end_marker = "Summary" if use_smart_truncate and entry.name == 'package.json' else "Content"
                                f.write(f"{content_prefix}    ---- End {end_marker} ----\n")
                            except UnicodeDecodeError:
                                # Handle files that cannot be decoded as UTF-8 (likely binary or corrupted text).
                                f.write(f"{content_prefix}    [Cannot decode file content]\n")
                            except Exception as e:
                                # Catch any other errors during file reading.
                                f.write(f"{content_prefix}    [Error reading file: {e}]\n")
                    except OSError as e:
                        # Handle errors when getting file statistics (e.g., broken symlinks).
                        f.write(f"{prefix}{connector}{TREE_CHARS['file']} {entry.name} [Stat Error]\n")

        walk_dir(root) # Start the recursive directory traversal from the root.

def main():
    """
    The main function of the script.

    It parses command-line arguments, sets up logging, validates the input path,
    and orchestrates the file tree generation process based on user-specified options.
    It can generate a summary view, a detailed content view, or both.
    """
    args = parse_arguments() # Parse all command-line arguments.
    setup_logging(args.log_level) # Configure logging based on the user's specified level.

    # Validate that the provided folder_path is indeed a directory.
    if not Path(args.folder_path).is_dir():
        logging.error(f"Error: Path '{args.folder_path}' is not a valid directory.")
        sys.exit(1) # Exit the script with an error code.

    # Determine effective flags based on user arguments and default behaviors.
    pretty_chars = args.pretty # True if --pretty was used.
    omit_file_sizes = not args.include_file_sizes # True if --include-file-sizes was NOT used (default is to omit).
    use_smart_truncate = not args.no_truncate # True if --no-truncate was NOT used (default is smart truncation).

    # Determine content display logic based on user arguments.
    # By default, both summary and content views are enabled.
    show_summary_view = True
    show_content_view = True

    # If --only-summary is used, disable the content view.
    if args.only_summary:
        show_content_view = False
    # If --only-content is used, disable the summary view.
    elif args.only_content:
        show_summary_view = False

    # Log initial information about the tree generation process.
    logging.info(f"Generating file tree for '{args.folder_path}' (depth: {args.depth})...")
    if use_smart_truncate:
        logging.info(f"Smart truncation enabled (limit: {args.truncate_limit} lines).")
    else:
        logging.info(f"Full file contents included (max size: {get_display_size(args.max_file_size)}).")
    if not pretty_chars:
        logging.info("Using compact character set for token optimization.")
    else:
        logging.info("Using pretty character set.")
    if omit_file_sizes:
        logging.info("File sizes omitted.")
    else:
        logging.info("File sizes included.")

    try:
        # If the summary view is enabled, generate it first.
        if show_summary_view:
            logging.info("Generating summary view...")
            create_file_tree(
                root_path=args.folder_path,
                output_file=args.output,
                depth=args.depth,
                exclude_patterns=args.exclude,
                no_gitignore=args.no_gitignore,
                pretty_chars=pretty_chars,
                omit_file_sizes=omit_file_sizes,
                show_content=False, # Summary view explicitly does NOT show content.
                use_smart_truncate=use_smart_truncate,
                truncate_limit=args.truncate_limit,
                max_file_size=args.max_file_size,
                is_summary_run=True, # Indicate this is the summary run.
                is_first_output_run=True # This is always the first write to the file if summary is shown.
            )
            
            # If the content view is also enabled, append it after the summary.
            if show_content_view:
                # Add a separator and header for the detailed view.
                with open(args.output, 'a', encoding='utf-8') as f:
                    f.write("\n" + "="*80 + "\n")
                    f.write("📋 DETAILED VIEW WITH FILE CONTENTS\n")
                    f.write("="*80 + "\n\n")
                
                logging.info("Generating detailed view with content...")
                create_file_tree(
                    root_path=args.folder_path,
                    output_file=args.output,
                    depth=args.depth,
                    exclude_patterns=args.exclude,
                    no_gitignore=args.no_gitignore,
                    pretty_chars=pretty_chars,
                    omit_file_sizes=omit_file_sizes,
                    show_content=True, # Detailed view explicitly DOES show content.
                    use_smart_truncate=use_smart_truncate,
                    truncate_limit=args.truncate_limit,
                    max_file_size=args.max_file_size,
                    is_summary_run=False, # Indicate this is NOT the summary run.
                    is_first_output_run=False # This is NOT the first write if summary was already written.
                )
        elif show_content_view: # If only content view is requested (no summary).
            logging.info("Generating detailed view with content...")
            create_file_tree(
                root_path=args.folder_path,
                output_file=args.output,
                depth=args.depth,
                exclude_patterns=args.exclude,
                no_gitignore=args.no_gitignore,
                pretty_chars=pretty_chars,
                omit_file_sizes=omit_file_sizes,
                show_content=True, # Only content view explicitly DOES show content.
                use_smart_truncate=use_smart_truncate,
                truncate_limit=args.truncate_limit,
                max_file_size=args.max_file_size,
                is_summary_run=False, # Indicate this is NOT the summary run.
                is_first_output_run=True # This is the first and only write operation.
            )
        else: # This case should ideally not be reached due to argparse's mutual exclusivity.
            logging.warning("No output mode selected. No tree will be generated.")

        logging.info(f"File tree generation completed successfully. Output saved to '{args.output}'.")
    except Exception as e:
        # Catch any unexpected errors during the tree generation process.
        logging.critical(f"An unexpected error occurred: {e}")
        sys.exit(1) # Exit the script with an error code.

if __name__ == "__main__":
    # This block ensures that main() is called only when the script is executed directly,
    # not when it's imported as a module into another script.
    main()