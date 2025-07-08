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
python3 tree_gen.py [OPTIONS] [FOLDER_PATH] [DEPTH]
```

*   `FOLDER_PATH`: The path to the root folder you want to generate the tree for. Defaults to the current directory (`.`).
*   `DEPTH`: The maximum depth of subdirectories to include. Defaults to `6`.

### ⚙️ Command-Line Arguments:

*   `<FOLDER_PATH>` (optional, positional): The path to the root folder you want to generate the tree for. Defaults to the current directory (`.`).
*   `<DEPTH>` (optional, positional): The maximum depth of subdirectories to traverse. Files/directories beyond this depth will not be included. Defaults to `6`.

*   `--pretty`: Use a visually appealing, human-readable character set (e.g., `├── `, `│   `) for the tree structure. By default, a compact character set (` + `, ` | `) is used for better token optimization.
*   `--include-file-sizes`: Include the size of each file in the output (e.g., `file.txt [1.2 KB]`). By default, file sizes are omitted.
*   `--only-summary`: Generate only the summary file tree (directory and file names), without including any file contents. This option is mutually exclusive with `--only-content`.
*   `--only-content`: Generate only the detailed file tree that includes file contents, without the initial summary view. This option is mutually exclusive with `--only-summary`.
*   `--no-truncate`: Disable smart content truncation. This will include the full content of files up to the `--max-file-size` limit. By default, smart truncation is applied.
*   `--truncate-limit <lines>`: When smart truncation is enabled, this specifies the number of lines to show from the head and tail of a file. Default: `500` lines (500 from start, 500 from end).
*   `--max-file-size <bytes>`: Maximum file size (in bytes) for content inclusion. Files larger than this limit will have their content omitted, regardless of truncation settings. Default: `512KB` (524288 bytes).
*   `--output <filename>`: The name of the output file where the generated file tree will be saved. Default: `'file_tree.txt'`.
*   `--exclude <name1> <name2> ...`: Provide additional file or directory names to exclude from the tree. These are added to the `DEFAULT_EXCLUDES` list.
*   `--no-gitignore`: Ignore any `.gitignore` files found in the directory structure. By default, `.gitignore` rules are respected.
*   `--log-level <level>`: Sets the logging verbosity level for the script. Choices: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Default: `INFO`.

### 💡 Examples:

1.  **Generate a smart-truncated tree of the current directory (`.`) up to depth 4, saving the output to `tree.txt`:**
    ```bash
    python3 tree_gen.py . 4 --output tree.txt
    ```
    *(Smart truncation is the default behavior unless `--no-truncate` is used.)*

2.  **Generate a tree of the current directory (`.`) up to depth 3, using pretty (human-readable) characters:**
    ```bash
    python3 tree_gen.py . 3 --pretty
    ```
    *(By default, compact characters are used for token optimization.)*

3.  **Generate both a summary tree and a detailed tree with full file content (up to max file size) for the current directory (`.`) up to depth 2:**
    ```bash
    python3 tree_gen.py . 2 --no-truncate
    ```
    *(This is the default behavior when neither `--only-summary` nor `--only-content` is specified. The script will first output the summary tree, followed by the detailed content tree.)*

4.  **Generate a tree of the current directory (`.`) up to depth 5, omitting file sizes:**
    ```bash
    python3 tree_gen.py . 5
    ```
    *(Omitting file sizes is the default behavior. To include them, use the `--include-file-sizes` flag.)*

5.  **Generate a tree and exclude specific directories and files (e.g., `my_secret_folder` and `temp_file.log`):**
    ```bash
    python3 tree_gen.py . --exclude my_secret_folder temp_file.log
    ```

## 🤝 Contributing:

Contributions are welcome! If you have suggestions for improvements, bug reports, or new features, please open an issue or submit a pull request on GitHub.

## 📄 License:

This project is open-sourced under the MIT License. See the `LICENSE` file for more details.
