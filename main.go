// treegen — high-performance file tree generator with concurrent content reading.
//
// Build once, run forever:
//   go build -o treegen .
//   ./treegen .
//
// Or run directly without building (compiles on the fly):
//   go run main.go .
// Usage Examples:
//
//   # Scan current directory (default depth 16, smart truncation on)
//   ./treegen .
//
//   # Scan a specific folder, depth 4, save to custom file
//   ./treegen /path/to/project --depth 4 --output tree.md
//
//   # Only the tree structure (no file contents)
//   ./treegen . --only-summary
//
//   # Only file contents (no tree structure)
//   ./treegen . --only-content
//
//   # Include file sizes in the tree
//   ./treegen . --only-summary --include-file-sizes
//
//   # Full file content (no truncation), max 1MB per file
//   ./treegen . --no-truncate --max-content-size 1048576
//
//   # Custom truncation: show first/last 100 lines of large files
//   ./treegen . --truncate-limit 100
//
//   # Exclude additional directories or files
//   ./treegen . --exclude vendor,tmp,.idea
//
//   # Ignore all .gitignore rules
//   ./treegen . --no-gitignore
//
//   # Crank up concurrency for large drives (e.g. 200 workers)
//   ./treegen /mnt/data --depth 10 --workers 200
//
//   # Quiet mode (errors only) + shallow scan
//   ./treegen . --depth 2 --log-level ERROR
//
//   # Show help
//   ./treegen --help
//   ./treegen -h

package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"
)

// ---------------------------------------------------------------------------
// Configuration Constants
// ---------------------------------------------------------------------------

// Tree drawing characters — matches the Python script's output style.
const (
	treeSpace  = "  "
	treeBranch = " | "
	treeTee    = " + "
	treeCorner = " L "
	treeDirSym = "d"
	treeFilSym = "f"
)

// defaultExcludes are directory/file names excluded from traversal by default.
var defaultExcludes = map[string]bool{
	".git": true, ".vscode": true, "node_modules": true, "__pycache__": true,
	"dist": true, "build": true, ".DS_Store": true, "coverage": true,
	".next": true, "out": true, "logs": true, ".env": true,
	"file_tree.md": true, "gemini_system_prompt.md": true, ".gitignore": true,
	"codebase.xml": true, "modifications.xml": true, "backups": true,
	"codebase_to_xml.py": true, "apply_xml_changes.py": true,
	"sw.js": true, ".swc": true, "tsconfig.jest.tsbuildinfo": true,
	"tree_gen.py": true, "treegen": true, "treegen.go": true,
}

// binaryExtensions are file suffixes whose content is always skipped.
var binaryExtensions = map[string]bool{
	".png": true, ".jpg": true, ".jpeg": true, ".gif": true, ".ico": true,
	".svg": true, ".eot": true, ".ttf": true, ".woff": true, ".woff2": true,
	".otf": true, ".zip": true, ".gz": true, ".db": true, ".tar": true,
	".bz2": true, ".xz": true, ".7z": true, ".rar": true, ".exe": true,
	".dll": true, ".so": true, ".dylib": true, ".pdf": true, ".mp3": true,
	".mp4": true, ".mov": true, ".avi": true, ".webm": true, ".webp": true,
	".bmp": true, ".tiff": true, ".psd": true, ".class": true, ".o": true,
	".a": true, ".pyc": true, ".pyo": true, ".wasm": true,
}

// lockFiles are filenames whose content is omitted for brevity.
var lockFiles = map[string]bool{
	"package-lock.json": true,
	"yarn.lock":         true,
	"pnpm-lock.yaml":    true,
}

// languageMap maps file extensions to Markdown code-fence language tags.
var languageMap = map[string]string{
	".py": "python", ".js": "javascript", ".jsx": "javascript",
	".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript",
	".tsx": "typescript", ".html": "html", ".css": "css",
	".json": "json", ".xml": "xml", ".kt": "kotlin", ".kts": "kotlin",
	".java": "java", ".md": "markdown", ".sh": "bash",
	".yml": "yaml", ".yaml": "yaml", ".go": "go", ".rb": "ruby",
	".php": "php", ".c": "c", ".cpp": "cpp", ".h": "c",
	".rs": "rust", ".swift": "swift", ".txt": "text",
	".gradle": "groovy", ".sql": "sql", ".pl": "perl",
	".toml": "toml", ".ini": "ini", ".env": "bash",
	".dockerfile": "dockerfile", ".tf": "hcl", ".r": "r",
	".lua": "lua", ".zig": "zig", ".nim": "nim", ".dart": "dart",
	".vue": "vue", ".svelte": "svelte", ".scss": "scss", ".less": "less",
	".graphql": "graphql", ".proto": "protobuf",
}

// ---------------------------------------------------------------------------
// CLI Options
// ---------------------------------------------------------------------------

type options struct {
	folderPath       string
	depth            int
	includeFileSizes bool
	onlySummary      bool
	onlyContent      bool
	noTruncate       bool
	truncateLimit    int
	maxFileSize      int64
	maxContentSize   int64
	output           string
	exclude          string // comma-separated
	noGitignore      bool
	logLevel         string
	workers          int
}

func parseArgs() options {
	o := options{}

	// Pre-process os.Args: extract positional arguments (non-flag tokens)
	// so that flags work regardless of position. Go's flag package stops
	// parsing at the first non-flag argument; this workaround moves
	// positional args to the end.
	var flagArgs []string
	var positionalArgs []string

	// knownValueFlags are flags that consume the next argument as their value.
	knownValueFlags := map[string]bool{
		"--depth": true, "--truncate-limit": true, "--max-file-size": true,
		"--max-content-size": true, "--output": true, "--exclude": true,
		"--log-level": true, "--workers": true,
	}

	args := os.Args[1:]
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "-") {
			flagArgs = append(flagArgs, a)
			// If this flag takes a value and the value is the next arg (not using =)
			if knownValueFlags[a] && !strings.Contains(a, "=") && i+1 < len(args) {
				i++
				flagArgs = append(flagArgs, args[i])
			}
		} else {
			positionalArgs = append(positionalArgs, a)
		}
	}
	// Reconstruct os.Args so flag.Parse sees flags first, then positionals.
	os.Args = append([]string{os.Args[0]}, append(flagArgs, positionalArgs...)...)

	flag.IntVar(&o.depth, "depth", 16, "Maximum directory depth to traverse")
	flag.BoolVar(&o.includeFileSizes, "include-file-sizes", false, "Include file sizes in tree output")
	flag.BoolVar(&o.onlySummary, "only-summary", false, "Generate only the tree summary (no file contents)")
	flag.BoolVar(&o.onlyContent, "only-content", false, "Generate only the detailed content view")
	flag.BoolVar(&o.noTruncate, "no-truncate", false, "Disable smart truncation; include full file content")
	flag.IntVar(&o.truncateLimit, "truncate-limit", 500, "Lines to show from head/tail when truncating")
	flag.Int64Var(&o.maxFileSize, "max-file-size", 512*1024, "Max file size in bytes for content inclusion (default 512KB)")
	flag.Int64Var(&o.maxContentSize, "max-content-size", 1024*1024, "Hard cap for reading file content in bytes (default 1MB)")
	flag.StringVar(&o.output, "output", "file_tree.md", "Output file path")
	flag.StringVar(&o.exclude, "exclude", "", "Comma-separated additional names to exclude")
	flag.BoolVar(&o.noGitignore, "no-gitignore", false, "Ignore .gitignore files")
	flag.StringVar(&o.logLevel, "log-level", "INFO", "Log level: DEBUG, INFO, WARNING, ERROR")
	flag.IntVar(&o.workers, "workers", runtime.NumCPU()*4, "Number of concurrent file-reading goroutines")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, `treegen — high-performance file tree generator

Usage:
  treegen [folder_path] [flags]

Examples:
  treegen . --depth 4 --output tree.md
  treegen /path/to/project --depth 2 --no-truncate
  treegen --only-summary --include-file-sizes .
  treegen . --depth 5 --workers 200

Flags:
`)
		flag.PrintDefaults()
	}

	flag.Parse()

	// Positional argument: folder path
	if flag.NArg() > 0 {
		o.folderPath = flag.Arg(0)
	} else {
		o.folderPath = "."
	}

	// Validate mutual exclusivity
	if o.onlySummary && o.onlyContent {
		fmt.Fprintf(os.Stderr, "Error: --only-summary and --only-content are mutually exclusive\n")
		os.Exit(1)
	}

	if o.workers < 1 {
		o.workers = 1
	}

	return o
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

type logLevel int

const (
	levelDEBUG logLevel = iota
	levelINFO
	levelWARNING
	levelERROR
)

var currentLogLevel logLevel

func setupLogging(level string) {
	switch strings.ToUpper(level) {
	case "DEBUG":
		currentLogLevel = levelDEBUG
	case "INFO":
		currentLogLevel = levelINFO
	case "WARNING":
		currentLogLevel = levelWARNING
	case "ERROR":
		currentLogLevel = levelERROR
	default:
		fmt.Fprintf(os.Stderr, "Invalid log level: %s. Defaulting to INFO.\n", level)
		currentLogLevel = levelINFO
	}
	log.SetOutput(os.Stderr)
	log.SetFlags(0)
}

func logMsg(lvl logLevel, format string, args ...any) {
	if lvl < currentLogLevel {
		return
	}
	prefix := "INFO"
	switch lvl {
	case levelDEBUG:
		prefix = "DEBUG"
	case levelWARNING:
		prefix = "WARNING"
	case levelERROR:
		prefix = "ERROR"
	}
	log.Printf("%s: %s", prefix, fmt.Sprintf(format, args...))
}

// ---------------------------------------------------------------------------
// Gitignore Engine
// ---------------------------------------------------------------------------

// gitignoreMatcher holds compiled patterns from a single .gitignore file.
type gitignoreMatcher struct {
	patterns []gitignorePattern
	baseDir  string
}

type gitignorePattern struct {
	pattern   string
	negation  bool
	dirOnly   bool
	hasSlash  bool // pattern contains a slash → match against relative path
	matchBase string
}

// parseGitignoreFile reads and compiles patterns from a .gitignore file.
func parseGitignoreFile(path string) *gitignoreMatcher {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()

	baseDir := filepath.Dir(path)
	m := &gitignoreMatcher{baseDir: baseDir}

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		p := gitignorePattern{}

		// Handle negation
		if strings.HasPrefix(line, "!") {
			p.negation = true
			line = line[1:]
		}

		// Handle directory-only patterns
		if strings.HasSuffix(line, "/") {
			p.dirOnly = true
			line = strings.TrimSuffix(line, "/")
		}

		// If pattern contains a slash (other than trailing), it's anchored
		p.hasSlash = strings.Contains(line, "/")

		// Remove leading slash for matching
		line = strings.TrimPrefix(line, "/")

		p.pattern = line
		p.matchBase = filepath.Base(line)
		m.patterns = append(m.patterns, p)
	}
	return m
}

// matches checks if a path is matched by this gitignore file.
// Returns: (matched bool, negated bool)
func (m *gitignoreMatcher) matches(fullPath string, isDir bool) (bool, bool) {
	relPath, err := filepath.Rel(m.baseDir, fullPath)
	if err != nil {
		return false, false
	}
	// Normalize to forward slashes for matching
	relPath = filepath.ToSlash(relPath)
	baseName := filepath.Base(fullPath)

	matched := false
	negated := false

	for _, p := range m.patterns {
		if p.dirOnly && !isDir {
			continue
		}

		var isMatch bool
		if p.hasSlash {
			// Match against relative path from .gitignore location
			isMatch, _ = filepath.Match(p.pattern, relPath)
			if !isMatch {
				// Try matching with ** semantics: pattern matches any subpath
				isMatch = matchGlob(p.pattern, relPath)
			}
		} else {
			// Match against basename only
			isMatch, _ = filepath.Match(p.pattern, baseName)
		}

		if isMatch {
			if p.negation {
				negated = true
				matched = false
			} else {
				matched = true
				negated = false
			}
		}
	}

	return matched, negated
}

// matchGlob provides extended glob matching supporting ** patterns.
func matchGlob(pattern, name string) bool {
	// Handle ** in patterns (matches any number of directories)
	if strings.Contains(pattern, "**") {
		parts := strings.SplitN(pattern, "**", 2)
		prefix := strings.TrimSuffix(parts[0], "/")
		suffix := strings.TrimPrefix(parts[1], "/")

		if prefix == "" && suffix == "" {
			return true
		}

		segments := strings.Split(name, "/")
		for i := range segments {
			subPath := strings.Join(segments[i:], "/")
			if suffix == "" {
				if prefix == "" {
					return true
				}
				m, _ := filepath.Match(prefix, strings.Join(segments[:i], "/"))
				if m {
					return true
				}
			} else {
				m, _ := filepath.Match(suffix, subPath)
				if m {
					if prefix == "" {
						return true
					}
					pm, _ := filepath.Match(prefix, strings.Join(segments[:i], "/"))
					if pm {
						return true
					}
				}
			}
		}
		// Also try matching the basename against suffix
		if suffix != "" {
			m, _ := filepath.Match(suffix, filepath.Base(name))
			if m {
				return true
			}
		}
		return false
	}

	// Direct match attempt
	m, _ := filepath.Match(pattern, name)
	return m
}

// gitignoreStack manages a stack of gitignore matchers as we descend directories.
type gitignoreStack struct {
	matchers []*gitignoreMatcher
}

func (s *gitignoreStack) push(m *gitignoreMatcher) {
	if m != nil {
		s.matchers = append(s.matchers, m)
	}
}

func (s *gitignoreStack) pop() {
	if len(s.matchers) > 0 {
		s.matchers = s.matchers[:len(s.matchers)-1]
	}
}

func (s *gitignoreStack) clone() *gitignoreStack {
	c := &gitignoreStack{
		matchers: make([]*gitignoreMatcher, len(s.matchers)),
	}
	copy(c.matchers, s.matchers)
	return c
}

// isExcluded checks the path against all gitignore matchers in the stack.
func (s *gitignoreStack) isExcluded(fullPath string, isDir bool) bool {
	// Process from innermost (most specific) to outermost
	for i := len(s.matchers) - 1; i >= 0; i-- {
		matched, negated := s.matchers[i].matches(fullPath, isDir)
		if negated {
			return false // negation pattern overrides
		}
		if matched {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------------------
// File Content Processing
// ---------------------------------------------------------------------------

// fileJob represents a file whose content needs to be read by a worker.
type fileJob struct {
	fullPath string
	relPath  string
	order    int // walk-discovery order for deterministic output
}

// contentResult holds the formatted content output for a single file.
type contentResult struct {
	relPath string
	content string
	order   int
}

// isBinaryContent checks the first 512 bytes for null bytes — same heuristic as git.
func isBinaryContent(data []byte) bool {
	checkLen := len(data)
	if checkLen > 512 {
		checkLen = 512
	}
	for i := 0; i < checkLen; i++ {
		if data[i] == 0 {
			return true
		}
	}
	return false
}

// truncateContent shows the first and last `limit` lines of content.
func truncateContent(content string, limit int, name string) string {
	lines := strings.Split(content, "\n")
	if len(lines) <= limit*2 {
		return content
	}
	head := strings.Join(lines[:limit], "\n")
	tail := strings.Join(lines[len(lines)-limit:], "\n")
	omitted := len(lines) - (limit * 2)
	logMsg(levelINFO, "Truncating %d lines from file %s", omitted, name)
	return head + "\n\n... [content truncated] ...\n\n" + tail
}

// summarizePackageJSON extracts key fields from a package.json file.
func summarizePackageJSON(content string) string {
	var data map[string]any
	if err := json.Unmarshal([]byte(content), &data); err != nil {
		return "[Could not parse package.json]"
	}
	summary := make(map[string]any)
	for _, key := range []string{"name", "version", "scripts", "dependencies", "devDependencies"} {
		if v, ok := data[key]; ok {
			summary[key] = v
		}
	}
	out, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		return "[Could not serialize package.json summary]"
	}
	return string(out)
}

// getDisplaySize formats byte counts to human-readable strings.
func getDisplaySize(sizeBytes int64) string {
	if sizeBytes < 1024 {
		return fmt.Sprintf("%d B", sizeBytes)
	}
	size := float64(sizeBytes)
	for _, unit := range []string{"KB", "MB", "GB", "TB"} {
		size /= 1024
		if size < 1024 {
			return fmt.Sprintf("%.1f %s", size, unit)
		}
	}
	return fmt.Sprintf("%.1f PB", size/1024)
}

// ---------------------------------------------------------------------------
// Progress Reporter
// ---------------------------------------------------------------------------

type progress struct {
	filesScanned  atomic.Int64
	bytesScanned  atomic.Int64
	dirsScanned   atomic.Int64
	filesRead     atomic.Int64
	filesSkipped  atomic.Int64
	startTime     time.Time
	done          chan struct{}
}

func newProgress() *progress {
	return &progress{
		startTime: time.Now(),
		done:      make(chan struct{}),
	}
}

func (p *progress) run() {
	ticker := time.NewTicker(1 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			elapsed := time.Since(p.startTime)
			files := p.filesScanned.Load()
			dirs := p.dirsScanned.Load()
			bytesVal := p.bytesScanned.Load()
			rate := float64(0)
			if elapsed.Seconds() > 0 {
				rate = float64(files) / elapsed.Seconds()
			}
			fmt.Fprintf(os.Stderr, "\r\033[K[ %s Scanned | %s files | %s dirs | %.0f files/sec | %s ]",
				getDisplaySize(bytesVal),
				formatCount(files),
				formatCount(dirs),
				rate,
				formatDuration(elapsed),
			)
		case <-p.done:
			// Print final stats
			elapsed := time.Since(p.startTime)
			files := p.filesScanned.Load()
			dirs := p.dirsScanned.Load()
			bytesVal := p.bytesScanned.Load()
			read := p.filesRead.Load()
			skipped := p.filesSkipped.Load()
			rate := float64(0)
			if elapsed.Seconds() > 0 {
				rate = float64(files) / elapsed.Seconds()
			}
			fmt.Fprintf(os.Stderr, "\r\033[K")
			fmt.Fprintf(os.Stderr, "✓ Done: %s files, %s dirs, %s scanned in %s (%.0f files/sec)\n",
				formatCount(files), formatCount(dirs), getDisplaySize(bytesVal),
				formatDuration(elapsed), rate,
			)
			fmt.Fprintf(os.Stderr, "  Content read: %s files | Skipped: %s files\n",
				formatCount(read), formatCount(skipped),
			)
			return
		}
	}
}

func formatCount(n int64) string {
	if n < 1000 {
		return fmt.Sprintf("%d", n)
	}
	if n < 1_000_000 {
		return fmt.Sprintf("%.1fK", float64(n)/1_000)
	}
	return fmt.Sprintf("%.1fM", float64(n)/1_000_000)
}

func formatDuration(d time.Duration) string {
	d = d.Round(time.Second)
	h := d / time.Hour
	d -= h * time.Hour
	m := d / time.Minute
	d -= m * time.Minute
	s := d / time.Second
	if h > 0 {
		return fmt.Sprintf("%d:%02d:%02d", h, m, s)
	}
	return fmt.Sprintf("%d:%02d", m, s)
}

// ---------------------------------------------------------------------------
// Exclusion Check
// ---------------------------------------------------------------------------

func shouldExclude(name, fullPath string, isDir bool, baseExclude map[string]bool, giStack *gitignoreStack) bool {
	if baseExclude[name] {
		logMsg(levelDEBUG, "Excluded by default/user pattern: %s", name)
		return true
	}
	if giStack != nil && giStack.isExcluded(fullPath, isDir) {
		logMsg(levelDEBUG, "Excluded by gitignore: %s", fullPath)
		return true
	}
	return false
}

// ---------------------------------------------------------------------------
// Core Walker — Single-Pass Streaming Architecture
// ---------------------------------------------------------------------------

type walker struct {
	opts          options
	root          string
	baseExclude   map[string]bool
	summaryBuf    *bytes.Buffer
	jobs          chan fileJob
	prog          *progress
	orderCounter  int
	showSummary   bool
	showContent   bool
	smartTruncate bool
}

func newWalker(opts options) (*walker, error) {
	absRoot, err := filepath.Abs(opts.folderPath)
	if err != nil {
		return nil, fmt.Errorf("resolving path %q: %w", opts.folderPath, err)
	}

	info, err := os.Stat(absRoot)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("path %q is not a valid directory", opts.folderPath)
	}

	// Build exclusion set
	exclude := make(map[string]bool)
	for k, v := range defaultExcludes {
		exclude[k] = v
	}
	if opts.exclude != "" {
		for _, e := range strings.Split(opts.exclude, ",") {
			e = strings.TrimSpace(e)
			if e != "" {
				exclude[e] = true
			}
		}
	}

	showSummary := true
	showContent := true
	if opts.onlySummary {
		showContent = false
	} else if opts.onlyContent {
		showSummary = false
	}

	return &walker{
		opts:          opts,
		root:          absRoot,
		baseExclude:   exclude,
		summaryBuf:    &bytes.Buffer{},
		jobs:          make(chan fileJob, opts.workers*4),
		prog:          newProgress(),
		showSummary:   showSummary,
		showContent:   showContent,
		smartTruncate: !opts.noTruncate,
	}, nil
}

// walk performs the single-pass directory traversal.
func (w *walker) walk() {
	giStack := &gitignoreStack{}

	// Load root-level .gitignore
	if !w.opts.noGitignore {
		m := parseGitignoreFile(filepath.Join(w.root, ".gitignore"))
		giStack.push(m)
	}

	rootName := filepath.Base(w.root)
	if w.showSummary {
		w.summaryBuf.WriteString(fmt.Sprintf("%s %s/\n", treeDirSym, rootName))
	}

	w.walkDir(w.root, "", 0, giStack)
	close(w.jobs)
}

func (w *walker) walkDir(currentPath, prefix string, depth int, giStack *gitignoreStack) {
	if depth >= w.opts.depth {
		return
	}

	w.prog.dirsScanned.Add(1)

	// Read and sort directory entries
	dirEntries, err := os.ReadDir(currentPath)
	if err != nil {
		logMsg(levelWARNING, "Could not access %s: %v", currentPath, err)
		return
	}

	// Sort: directories first, then case-insensitive alphabetical
	sort.Slice(dirEntries, func(i, j int) bool {
		iDir := dirEntries[i].IsDir()
		jDir := dirEntries[j].IsDir()
		if iDir != jDir {
			return iDir
		}
		return strings.ToLower(dirEntries[i].Name()) < strings.ToLower(dirEntries[j].Name())
	})

	// Filter excluded entries
	var filtered []fs.DirEntry
	for _, entry := range dirEntries {
		name := entry.Name()
		fullPath := filepath.Join(currentPath, name)
		if !shouldExclude(name, fullPath, entry.IsDir(), w.baseExclude, giStack) {
			filtered = append(filtered, entry)
		}
	}

	for i, entry := range filtered {
		isLast := i == len(filtered)-1
		connector := treeTee
		if isLast {
			connector = treeCorner
		}

		name := entry.Name()
		fullPath := filepath.Join(currentPath, name)

		if entry.IsDir() {
			// Write tree line
			if w.showSummary {
				w.summaryBuf.WriteString(fmt.Sprintf("%s%s%s %s/\n", prefix, connector, treeDirSym, name))
			}

			// Push gitignore for this directory
			pushed := false
			if !w.opts.noGitignore {
				giFile := filepath.Join(fullPath, ".gitignore")
				if _, err := os.Stat(giFile); err == nil {
					m := parseGitignoreFile(giFile)
					giStack.push(m)
					pushed = true
				}
			}

			// Recurse
			newPrefix := prefix + treeSpace
			if !isLast {
				newPrefix = prefix + treeBranch
			}
			w.walkDir(fullPath, newPrefix, depth+1, giStack)

			// Pop gitignore
			if pushed {
				giStack.pop()
			}
		} else {
			// Get file info
			info, err := entry.Info()
			if err != nil {
				logMsg(levelWARNING, "Could not stat %s: %v", fullPath, err)
				continue
			}

			size := info.Size()
			w.prog.filesScanned.Add(1)
			w.prog.bytesScanned.Add(size)

			// Write tree line
			if w.showSummary {
				sizeStr := ""
				if w.opts.includeFileSizes {
					sizeStr = fmt.Sprintf(" [%s]", getDisplaySize(size))
				}
				w.summaryBuf.WriteString(fmt.Sprintf("%s%s%s %s%s\n", prefix, connector, treeFilSym, name, sizeStr))
			}

			// Dispatch content reading job
			if w.showContent {
				relPath, _ := filepath.Rel(w.root, fullPath)
				w.orderCounter++
				w.jobs <- fileJob{
					fullPath: fullPath,
					relPath:  relPath,
					order:    w.orderCounter,
				}
			}
		}
	}
}

// ---------------------------------------------------------------------------
// Content Worker
// ---------------------------------------------------------------------------

func (w *walker) contentWorker(results chan<- contentResult, wg *sync.WaitGroup) {
	defer wg.Done()
	for job := range w.jobs {
		result := w.processFile(job)
		results <- result
	}
}

func (w *walker) processFile(job fileJob) contentResult {
	result := contentResult{
		relPath: job.relPath,
		order:   job.order,
	}

	info, err := os.Stat(job.fullPath)
	if err != nil {
		result.content = fmt.Sprintf("\n--- START OF FILE %s ---\n[Stat Error: %v]\n--- END OF FILE %s ---\n",
			job.relPath, err, job.relPath)
		w.prog.filesSkipped.Add(1)
		return result
	}

	var buf bytes.Buffer
	buf.WriteString(fmt.Sprintf("\n--- START OF FILE %s ---\n", job.relPath))

	size := info.Size()
	ext := strings.ToLower(filepath.Ext(job.fullPath))
	name := filepath.Base(job.fullPath)

	// Check size cap
	if size > w.opts.maxContentSize {
		buf.WriteString(fmt.Sprintf("[File content omitted, size %s > %s]\n",
			getDisplaySize(size), getDisplaySize(w.opts.maxContentSize)))
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Check binary extension
	if binaryExtensions[ext] {
		buf.WriteString("[Binary/SVG content omitted]\n")
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Check lock files
	if w.smartTruncate && lockFiles[name] {
		buf.WriteString("[Lock file content omitted for brevity]\n")
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Read file content
	f, err := os.Open(job.fullPath)
	if err != nil {
		buf.WriteString(fmt.Sprintf("[Error reading file: %v]\n", err))
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}
	defer f.Close()

	// Read up to maxContentSize + 1 to detect if file is over limit
	readLimit := w.opts.maxContentSize + 1
	data, err := io.ReadAll(io.LimitReader(f, readLimit))
	if err != nil {
		buf.WriteString(fmt.Sprintf("[Error reading file: %v]\n", err))
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Secondary size check (for files that grew since stat)
	if int64(len(data)) > w.opts.maxContentSize {
		buf.WriteString(fmt.Sprintf("[File content omitted, size > %s]\n",
			getDisplaySize(w.opts.maxContentSize)))
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Binary detection heuristic: check first 512 bytes for null bytes
	if isBinaryContent(data) {
		buf.WriteString("[Binary content detected, omitted]\n")
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Validate UTF-8
	content := string(data)
	if !utf8.ValidString(content) {
		buf.WriteString("[Cannot decode file content — not valid UTF-8]\n")
		buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))
		result.content = buf.String()
		w.prog.filesSkipped.Add(1)
		return result
	}

	// Apply smart truncation
	contentToWrite := content
	if w.smartTruncate {
		if name == "package.json" {
			contentToWrite = summarizePackageJSON(content)
		} else {
			contentToWrite = truncateContent(content, w.opts.truncateLimit, name)
		}
	}

	// Write with language-tagged code fence
	lang := languageMap[ext]
	buf.WriteString(fmt.Sprintf("```%s\n", lang))
	buf.WriteString(contentToWrite)
	if !strings.HasSuffix(contentToWrite, "\n") {
		buf.WriteByte('\n')
	}
	buf.WriteString("```\n")
	buf.WriteString(fmt.Sprintf("--- END OF FILE %s ---\n", job.relPath))

	result.content = buf.String()
	w.prog.filesRead.Add(1)
	return result
}

// ---------------------------------------------------------------------------
// Output Writer
// ---------------------------------------------------------------------------

func (w *walker) writeOutput(results []contentResult) error {
	f, err := os.Create(w.opts.output)
	if err != nil {
		return fmt.Errorf("creating output file: %w", err)
	}
	defer f.Close()

	writer := bufio.NewWriterSize(f, 256*1024) // 256KB buffer for write performance
	defer writer.Flush()

	rootName := filepath.Base(w.root)

	// Write summary section
	if w.showSummary {
		fmt.Fprintf(writer, "# File Tree Summary for %s\n\n", rootName)
		fmt.Fprintf(writer, "```tree\n")
		writer.Write(w.summaryBuf.Bytes())
		fmt.Fprintf(writer, "```\n")
	}

	// Write content section
	if w.showContent && len(results) > 0 {
		if w.showSummary {
			fmt.Fprintf(writer, "\n\n# DETAILED VIEW WITH FILE CONTENTS\n")
		}

		// Sort by discovery order for deterministic output
		sort.Slice(results, func(i, j int) bool {
			return results[i].order < results[j].order
		})

		for _, r := range results {
			writer.WriteString(r.content)
		}
	}

	return nil
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

func main() {
	opts := parseArgs()
	setupLogging(opts.logLevel)

	w, err := newWalker(opts)
	if err != nil {
		logMsg(levelERROR, "%v", err)
		os.Exit(1)
	}

	logMsg(levelINFO, "Generating file tree for %q (depth: %d, workers: %d)...",
		opts.folderPath, opts.depth, opts.workers)

	if w.smartTruncate {
		logMsg(levelINFO, "Smart truncation enabled (limit: %d lines)", opts.truncateLimit)
	} else {
		logMsg(levelINFO, "Full file contents included (max size: %s)", getDisplaySize(opts.maxFileSize))
	}

	// Start progress reporter
	go w.prog.run()

	// Collect content results
	var results []contentResult
	var resultsMu sync.Mutex
	var workerWg sync.WaitGroup

	if w.showContent {
		resultsChan := make(chan contentResult, opts.workers*4)

		// Start content workers
		workerWg.Add(opts.workers)
		for i := 0; i < opts.workers; i++ {
			go w.contentWorker(resultsChan, &workerWg)
		}

		// Collector goroutine — gathers results into a slice
		var collectorWg sync.WaitGroup
		collectorWg.Add(1)
		go func() {
			defer collectorWg.Done()
			for r := range resultsChan {
				resultsMu.Lock()
				results = append(results, r)
				resultsMu.Unlock()
			}
		}()

		// Start walking (this is the producer — blocks until walk is complete)
		w.walk()

		// Wait for workers to finish, then close results channel
		workerWg.Wait()
		close(resultsChan)

		// Wait for collector
		collectorWg.Wait()
	} else {
		// Summary only — just walk, no workers needed
		w.walk()
	}

	// Signal progress reporter to print final stats and stop
	close(w.prog.done)
	// Give the progress goroutine a moment to print
	time.Sleep(50 * time.Millisecond)

	// Write output
	if err := w.writeOutput(results); err != nil {
		logMsg(levelERROR, "Failed to write output: %v", err)
		os.Exit(1)
	}

	logMsg(levelINFO, "Output saved to %q", opts.output)
}
