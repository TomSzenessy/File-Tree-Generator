# 🌳 `tree_gen.py`: Smart File Tree Generator

`tree_gen.py` is a Python script designed to generate a comprehensive file tree representation of a given directory. It intelligently handles large files and directories by offering smart content truncation, respects `.gitignore` rules, and provides various output options suitable for documentation, code analysis, or sharing project structures.

## ✨ Features

*   **Intelligent Content Truncation**: Automatically truncates large files to show only the head and tail, saving space and improving readability. Special handling for `package.json` to summarize key information.
*   **`.gitignore` Support**: Automatically respects `.gitignore` files to exclude specified files and directories from the tree.
*   **Customizable Depth**: Control the maximum depth of subdirectories to traverse.
*   **Output Formats**: Choose between a "pretty" human-readable tree or a "compact" token-optimized format.
*   **File Size Inclusion**: Option to include file sizes in the output.
*   **Summary and Detailed Views**: Generate an initial summary tree and/or a detailed tree with file contents.
*   **Exclusion Patterns**: Add custom exclusion patterns via command-line arguments.
*   **Logging**: Configurable logging levels for debugging and information.

## 🚀 Setup

`tree_gen.py` is a standalone Python script and does not require any external dependencies beyond the standard Python library.

1.  **Clone the repository (or download the script):**
    ```bash
    git clone https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git
    cd YOUR_REPOSITORY_NAME
    ```
    (Replace `YOUR_USERNAME` and `YOUR_REPOSITORY_NAME` with your actual GitHub details.)

2.  **Ensure you have Python installed:**
    This script requires Python 3.6 or newer. You can check your Python version with:
    ```bash
    python3 --version
    ```

## 💡 Usage

Run the script from your terminal. The basic syntax is:

```bash
python3 tree_gen.py [FOLDER_PATH] [DEPTH] [OPTIONS]
```

*   `FOLDER_PATH`: The path to the root folder you want to generate the tree for. Defaults to the current directory (`.`).
*   `DEPTH`: The maximum depth of subdirectories to include. Defaults to `6`.

### Command-Line Arguments:

*   `folder_path` (optional): Path to the root folder. Defaults to current directory (`.`).
*   `depth` (optional): Maximum depth of subdirectories. Defaults to `6`.
*   `--pretty`: Use a pretty, human-readable character set for the tree structure (default is compact).
*   `--include-file-sizes`: Include file sizes in the output (default is to omit).
*   `--only-summary`: Only generate the summary file tree, without file contents.
*   `--only-content`: Only generate the detailed file tree with content, without the initial summary view.
*   `--no-truncate`: Do not truncate file content; include full content up to `max-file-size` (default is smart truncation).
*   `--truncate-limit <lines>`: Number of lines for head/tail in smart truncation. Default: `500`.
*   `--max-file-size <bytes>`: Max file size for content inclusion. Default: `512KB` (524288 bytes).
*   `--output <filename>`: Output file name. Default: `'file_tree.txt'`.
*   `--exclude <name1> <name2> ...`: Additional file or directory names to exclude.
*   `--no-gitignore`: Ignore `.gitignore` files.
*   `--log-level <level>`: Logging verbosity. Choices: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Default: `INFO`.

### Examples:

1.  **Generate a smart-truncated tree of the current directory up to depth 4, output to `tree.txt`:**
    ```bash
    python3 tree_gen.py . 4 --output tree.txt
    ```

2.  **Generate a compact, smart-truncated tree of the current directory up to depth 3:**
    ```bash
    python3 tree_gen.py . 3 --compact
    ```
    (Note: `--compact` is the default, so you can omit it if not using `--pretty`)

3.  **Generate a summary tree, then a detailed tree with full file content (up to max file size):**
    ```bash
    python3 tree_gen.py . 2 --no-truncate --only-summary --only-content
    ```
    (Note: `--only-summary` and `--only-content` are mutually exclusive if used alone. To get both, you run without either, and it will generate summary then content. The example in the script's help text `%(prog)s . 2 --include-content --include-summary` implies a combined mode, which is the default behavior when neither `--only-summary` nor `--only-content` is specified. Let's correct this to reflect the actual script logic.)
    
    *Correction for combined summary and content view:*
    To get both summary and detailed content views, simply run the script without `--only-summary` or `--only-content`:
    ```bash
    python3 tree_gen.py . 2 --no-truncate
    ```
    This will first output the summary tree, followed by the detailed content tree.

4.  **Generate a tree without file sizes for maximum token saving:**
    ```bash
    python3 tree_gen.py . 5 --include-file-sizes
    ```
    (Note: `--include-file-sizes` is the flag to *include* them, so to omit them, you simply don't use the flag, as omission is the default. Let's correct this example.)

    *Correction for omitting file sizes:*
    To omit file sizes (which is the default behavior), simply do not include the `--include-file-sizes` flag:
    ```bash
    python3 tree_gen.py . 5
    ```
    If you *want* to include them, use:
    ```bash
    python3 tree_gen.py . 5 --include-file-sizes
    ```

5.  **Exclude specific directories and files:**
    ```bash
    python3 tree_gen.py . --exclude my_secret_folder temp_file.log
    ```

## 🤝 Contributing

Contributions are welcome! If you have suggestions for improvements, bug reports, or new features, please open an issue or submit a pull request on GitHub.

## 📄 License

This project is open-sourced under the MIT License. See the `LICENSE` file for more details.
