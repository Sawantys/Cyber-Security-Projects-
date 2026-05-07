# System Architecture

This document breaks down how the system is designed and why certain architectural decisions were made.

## High Level Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    Operating System                       │
│                  (Keyboard Event Stream)                  │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  pynput Listener     │
              │  (Event Callbacks)   │
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │     Keylogger        │
              │   (Main Controller)  │
              └─────┬────────┬───────┘
                    │        │
        ┌───────────┘        └──────────┐
        ▼                               ▼
┌───────────────┐              ┌────────────────┐
│ WindowTracker │              │  LogManager    │
│  (Platform-   │              │ (File Writing) │
│   Specific)   │              └────────┬───────┘
└───────────────┘                       │
                                        ▼
                              ┌──────────────────┐
                              │ WebhookDelivery  │
                              │  (Exfiltration)  │
                              └──────────────────┘
```

### Component Breakdown

**Keylogger (Main Controller)**
- Purpose: Orchestrates all components and handles the event processing pipeline
- Responsibilities: Receives keyboard events from pynput, processes keys, coordinates window tracking, delegates to logging and webhook delivery
- Interfaces: Exposes `start()` and `stop()` methods for lifecycle management, registers `_on_press()` callback with pynput listener
- Location: `keylogger.py:293-424`

**LogManager**
- Purpose: Manages persistent storage of keystroke events with automatic file rotation
- Responsibilities: Creates timestamped log files, writes events to disk, monitors file size and rotates when limit reached, provides thread-safe access via locks
- Interfaces: `write_event(event)` for logging, `get_current_log_content()` for reading back logs
- Location: `keylogger.py:168-218`

**WebhookDelivery**
- Purpose: Handles remote exfiltration of captured keystrokes via HTTP webhooks
- Responsibilities: Buffers events to reduce network traffic, batches events before sending, delivers JSON payloads to configured endpoint, handles delivery failures gracefully
- Interfaces: `add_event(event)` for queuing, `flush()` for forcing immediate delivery
- Location: `keylogger.py:221-263`

**WindowTracker**
- Purpose: Determines which application has focus when keystrokes occur
- Responsibilities: Platform detection (Windows/macOS/Linux), calls platform-specific APIs to get active window title, provides unified interface across platforms
- Interfaces: Static method `get_active_window()` returns current window title or None
- Location: `keylogger.py:121-165`

**KeyEvent (Data Model)**
- Purpose: Immutable representation of a single keystroke with metadata
- Responsibilities: Stores timestamp, key value, window context, and key type classification
- Interfaces: `to_dict()` for JSON serialization, `to_log_string()` for human-readable formatting
- Location: `keylogger.py:84-107`

## Data Flow

### Primary Use Case: Keystroke Capture and Logging

Step by step walkthrough of what happens when a user presses a key:

```
1. OS Keyboard Event → pynput Listener
   User presses 'a' key, OS delivers event to all registered listeners
   pynput captures event and triggers our callback

2. Listener → Keylogger._on_press() (keylogger.py:367)
   Callback receives Key or KeyCode object
   Checks if it's the toggle key (F9) → pause/resume if so
   Checks if logging is active → early return if paused

3. Keylogger → WindowTracker.get_active_window() (keylogger.py:360)
   Calls platform-specific code to get active window
   Caches result for 0.5 seconds to avoid excessive API calls
   Returns window title like "Chrome - Gmail" or None

4. Keylogger → _process_key() (keylogger.py:322)
   Converts Key/KeyCode to string representation
   Maps special keys (Enter→"[ENTER]", Space→"[SPACE]")
   Classifies key type (CHAR, SPECIAL, UNKNOWN)

5. Keylogger → Creates KeyEvent (keylogger.py:373)
   Bundles timestamp, key string, window title, and key type
   Creates immutable dataclass instance

6. Keylogger → LogManager.write_event() (keylogger.py:184)
   Acquires lock for thread safety
   Formats event to log string: "[2025-01-31 14:30:22][Chrome] a"
   Writes to current log file via Python logging
   Checks file size and rotates if needed

7. Keylogger → WebhookDelivery.add_event() (keylogger.py:222)
   Adds event to buffer array
   Checks if buffer reached batch size (default 50)
   If full, serializes all events to JSON and POST to webhook
```

Example with code references:
```
1. User types "p" → OS delivers KeyCode(char='p')

2. _on_press receives event (keylogger.py:367-383)
   Validates logging is active, not the toggle key

3. _update_active_window() called (keylogger.py:352-362)
   Returns "Visual Studio Code - keylogger.py"

4. _process_key(KeyCode(char='p')) → ("p", KeyType.CHAR)
   Not a special key, has .char attribute

5. KeyEvent created:
   timestamp=datetime.now()
   key="p"  
   window_title="Visual Studio Code - keylogger.py"
   key_type=KeyType.CHAR

6. LogManager.write_event() (keylogger.py:184-189)
   Writes: "[2025-01-31 14:30:45][Visual Studio Code - keylogger.py] p"
   Checks: Current file is 4.2 MB, under 5 MB limit, no rotation

7. WebhookDelivery.add_event() (keylogger.py:222-232)
   Buffer now has 47 events, not yet at batch size 50
```

### Secondary Use Case: Log File Rotation

Step by step for when log file grows too large:

```
1. LogManager.write_event() → _check_rotation() (keylogger.py:191)
   After writing event, checks current log file size
   
2. _check_rotation() (keylogger.py:191-208)
   Reads file size: 5.1 MB (over 5 MB limit)
   Closes current logging handler
   Removes handler from logger

3. _get_new_log_path() (keylogger.py:180)
   Generates new filename with current timestamp
   Format: "keylog_20250131_143500.txt"
   Returns Path object in log_dir

4. Creates new FileHandler
   Opens new file for writing
   Configures formatter (plain message, no log level)
   Adds handler back to logger

5. Next write_event() call → Goes to new file
   Old file preserved with all historical keystrokes
```

## Design Patterns

### Observer Pattern (Event-Driven Architecture)

**What it is:**
The Observer pattern allows objects to subscribe to events and react when they occur. The subject (keyboard) notifies observers (our callback) without tight coupling.

**Where we use it:**
pynput's `keyboard.Listener` implements the Observer pattern (`keylogger.py:407-413`):

```python
self.listener = keyboard.Listener(on_press=self._on_press)
self.listener.start()
```

Our `_on_press` method is the observer callback. When the OS delivers a keyboard event, pynput notifies us by calling this function.

**Why we chose it:**
Observer pattern is ideal for event-driven systems where we don't control the timing of events. We can't poll the keyboard (too slow, high CPU), we need to react immediately when keys are pressed. The pattern also decouples us from pynput's implementation details.

**Trade-offs:**
- Pros: Clean separation between event source and handler, enables real-time processing, scales to multiple event types (we could add mouse events)
- Cons: Callback runs in pynput's thread so we need careful synchronization, harder to debug than sequential code, callback failures can crash the listener

### Thread Safety with Locks

**What it is:**
Multiple threads accessing shared data requires synchronization primitives like locks to prevent race conditions.

**Where we use it:**
LogManager uses a lock around file operations (`keylogger.py:187-189`):

```python
def write_event(self, event: KeyEvent) -> None:
    with self.lock:
        self.logger.info(event.to_log_string())
        self._check_rotation()
```

WebhookDelivery also uses a lock for the event buffer (`keylogger.py:225-232`):

```python
def add_event(self, event: KeyEvent) -> None:
    with self.buffer_lock:
        self.event_buffer.append(event)
        if len(self.event_buffer) >= self.config.webhook_batch_size:
            self._deliver_batch()
```

**Why we chose it:**
The pynput callback runs in a separate thread from our main program. Without locks, simultaneous file writes could corrupt the log file. Similarly, the event buffer could have race conditions if accessed from multiple threads.

**Trade-offs:**
- Pros: Prevents data corruption, ensures consistency, simple to reason about (lock → access → unlock)
- Cons: Potential performance bottleneck (though keyboard events are slow enough this doesn't matter), risk of deadlock if locks acquired in wrong order (we only use one lock per component so this isn't an issue)

### Immutable Data with Dataclasses

**What it is:**
Dataclasses provide a clean syntax for creating classes that primarily store data. Making them immutable (frozen) prevents accidental modification.

**Where we use it:**
KeyEvent represents an immutable keystroke (`keylogger.py:84-107`):

```python
@dataclass
class KeyEvent:
    timestamp: datetime
    key: str
    window_title: str | None = None
    key_type: KeyType = KeyType.CHAR
```

KeyloggerConfig stores configuration (`keylogger.py:64-82`):

```python
@dataclass
class KeyloggerConfig:
    log_dir: Path = Path.home() / ".keylogger_logs"
    log_file_prefix: str = "keylog"
    max_log_size_mb: float = 5.0
    # ... more fields
```

**Why we chose it:**
Dataclasses reduce boilerplate (no need to write `__init__`, `__repr__`, etc). Type hints make the data structure self-documenting. Immutability prevents bugs where events get modified after creation.

**Trade-offs:**
- Pros: Less code, better type safety, automatic equality comparison, clear data structure
- Cons: Slightly less flexible than regular classes, can't be modified after creation (though this is intentional)

## Layer Separation

The architecture has a clear separation between concerns:

```
┌─────────────────────────────────────────────────┐
│           Application Layer                     │
│  - Keylogger main class                         │
│  - Lifecycle management (start/stop)            │
│  - Event processing pipeline                    │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────┴───────────────────────────────┐
│           Service Layer                         │
│  - LogManager (persistence)                     │
│  - WebhookDelivery (exfiltration)               │
│  - WindowTracker (context gathering)            │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────┴───────────────────────────────┐
│           Data Layer                            │
│  - KeyEvent (event representation)              │
│  - KeyloggerConfig (configuration)              │
│  - KeyType (enum classification)                │
└─────────────────────────────────────────────────┘
```

### Why Layers?

Layers enable independent modification. We can swap LogManager for a database writer without touching Keylogger. We can add new exfiltration methods alongside WebhookDelivery. Testing is easier since we can mock service layer components.

### What Lives Where

**Application Layer:**
- Files: Main Keylogger class (`keylogger.py:293-424`)
- Imports: Can import from service and data layers
- Forbidden: Direct file I/O (delegates to LogManager), HTTP requests (delegates to WebhookDelivery)

**Service Layer:**
- Files: LogManager (`keylogger.py:168-218`), WebhookDelivery (`keylogger.py:221-263`), WindowTracker (`keylogger.py:121-165`)
- Imports: Can import data layer, should not import application layer
- Forbidden: Knowledge of Keylogger implementation details, accessing pynput directly

**Data Layer:**
- Files: KeyEvent (`keylogger.py:84-107`), KeyloggerConfig (`keylogger.py:64-82`), KeyType (`keylogger.py:57-62`)
- Imports: Only standard library (datetime, pathlib, enum)
- Forbidden: Business logic, I/O operations, external dependencies

## Data Models

### KeyEvent

```python
@dataclass
class KeyEvent:
    timestamp: datetime
    key: str
    window_title: str | None = None
    key_type: KeyType = KeyType.CHAR
```

**Fields explained:**
- `timestamp`: When the keystroke occurred, used for log chronology and forensics. DateTime includes timezone info via `datetime.now()`.
- `key`: String representation of the key pressed. Either a single character ("a") or a bracketed special key ("[ENTER]"). Never empty.
- `window_title`: Context about where the keystroke occurred. None if window tracking disabled or platform unsupported. Format varies by platform (Windows includes process name, macOS just app name).
- `key_type`: Classification (CHAR/SPECIAL/UNKNOWN) used to filter special keys if `log_special_keys` is False in config.

**Relationships:**
KeyEvent is created by Keylogger, consumed by LogManager and WebhookDelivery. It's the universal data structure that flows through the entire pipeline.

### KeyloggerConfig

```python
@dataclass
class KeyloggerConfig:
    log_dir: Path = Path.home() / ".keylogger_logs"
    log_file_prefix: str = "keylog"
    max_log_size_mb: float = 5.0
    webhook_url: str | None = None
    webhook_batch_size: int = 50
    toggle_key: Key = Key.f9
    enable_window_tracking: bool = True
    log_special_keys: bool = True
```

**Fields explained:**
- `log_dir`: Where log files are stored. Default `~/.keylogger_logs` is hidden on Unix. Created automatically via `__post_init__`.
- `log_file_prefix`: Prefix for log filenames. Combined with timestamp to create unique files like "keylog_20250131_143022.txt".
- `max_log_size_mb`: File size limit in megabytes before rotation. 5MB default balances stealth (not too large) with minimizing file count.
- `webhook_url`: Optional remote endpoint for exfiltration. If None, only local logging occurs. Must be HTTPS for security.
- `webhook_batch_size`: Number of keystrokes to buffer before sending. Higher values reduce network noise but increase data loss risk if program crashes.
- `toggle_key`: Hotkey to pause/resume logging. Default F9 is unlikely to be pressed accidentally but easy to reach.
- `enable_window_tracking`: Whether to capture active window titles. Adds context but requires platform-specific dependencies.
- `log_special_keys`: Whether to log [SHIFT], [CTRL], etc. Set False to reduce log size and focus on printable characters.

**Relationships:**
Passed to all service layer components (LogManager, WebhookDelivery, Keylogger). Centralized configuration avoids passing individual parameters.

### KeyType Enum

```python
class KeyType(Enum):
    CHAR = auto()
    SPECIAL = auto()
    UNKNOWN = auto()
```

Classifies keys for filtering and logging decisions. CHAR is printable characters (a-z, 0-9, symbols). SPECIAL is control keys (Enter, Tab, arrows). UNKNOWN is for edge cases where key classification fails.

## Security Architecture

### Threat Model

What we're protecting against:
1. **Detection by Antivirus** - AV scans for known malware signatures, behavioral patterns, and suspicious API calls. Our keylogger uses legitimate libraries (pynput) which reduces signature detection but behavioral analysis might flag keyboard hooks.
2. **Network Monitoring** - Corporate networks monitor traffic for data exfiltration. HTTPS webhook delivery encrypts content but traffic analysis could detect periodic POST requests to external domains.
3. **User Suspicion** - If log files grow too large, rotate too frequently, or create disk I/O spikes, users might investigate. Performance impact from processing every keystroke could also raise red flags.

What we're NOT protecting against (out of scope):
- Kernel-level monitoring or EDR that hooks system calls below our privilege level
- Memory forensics that scan RAM for keystroke buffers
- Hardware keyloggers or BIOS-level monitoring
- Physical access to the machine for disk forensics

### Defense Layers

Our layered security approach (from the attacker's perspective):

```
Layer 1: Execution Prevention
    ↓ (bypassed if user runs the program)
Layer 2: Behavioral Detection
    ↓ (evaded via legitimate API usage)
Layer 3: Network Monitoring
    ↓ (mitigated with HTTPS and batching)
Layer 4: Forensic Detection
    ↓ (requires active investigation)
```

**Why multiple layers?**
Defense in depth assumes each layer can be bypassed but makes detection harder. If antivirus misses us (Layer 1), network monitoring might catch exfiltration (Layer 3). If we run on a laptop that's never inspected, we persist indefinitely despite forensic detectability (Layer 4).

## Storage Strategy

### Local File Storage

**What we store:**
- Timestamped keystroke events with window context
- Plain text format for easy exfiltration and reading
- Multiple files via rotation to avoid suspiciously large files

**Why this storage:**
Files are simple, don't require external dependencies (database), and are easy to exfiltrate (just upload the directory). Plain text trades security for simplicity since this is an educational project. Production malware would encrypt logs.

**Schema design:**
```
[2025-01-31 14:30:22][Chrome - Gmail] p
[2025-01-31 14:30:22][Chrome - Gmail] a
[2025-01-31 14:30:22][Chrome - Gmail] s
[2025-01-31 14:30:22][Chrome - Gmail] s
[2025-01-31 14:30:23][Chrome - Gmail] [ENTER]
```

Each line is independent. Chronological ordering simplifies reading. Window context in brackets enables filtering by application during analysis.

### In-Memory Buffering

WebhookDelivery maintains an in-memory buffer of KeyEvent objects before batch delivery. This reduces network calls but risks data loss if the program crashes before flush. Trade-off favors stealth over completeness.

## Configuration

### Environment Variables

The project doesn't use environment variables by default. Configuration is hardcoded in `main()` (`keylogger.py:427-436`). This avoids dependencies on shell environment but makes it harder to change config without modifying code.

For production use, you'd add environment variable support:
```python
config = KeyloggerConfig(
    webhook_url=os.getenv("KEYLOGGER_WEBHOOK_URL"),
    max_log_size_mb=float(os.getenv("KEYLOGGER_MAX_SIZE_MB", "5.0"))
)
```

### Configuration Strategy

**Development:**
Hardcoded config with webhook disabled, local logging to visible directory for easy testing. Toggle key enabled for quick pause during debugging.

**Production:**
Would load config from encrypted file or remote C2 server. Log directory hidden (`.keylogger_logs` with leading dot on Unix, `AppData/Local` on Windows). Webhook enabled with obfuscated domain. Toggle key disabled to prevent accidental discovery.

## Performance Considerations

### Bottlenecks

Where this system gets slow under load:
1. **File I/O on every keystroke** - Writing to disk for each event creates I/O contention. Mitigated by Python's logging module which buffers writes, but high keystroke rates (fast typist or gaming) could still cause lag.
2. **Window title lookups** - Platform APIs (win32gui, NSWorkspace, xdotool subprocess) have latency. We cache window title for 0.5 seconds (`keylogger.py:357-362`) to reduce API calls from thousands per second to ~2 per second.
3. **Webhook HTTP requests** - Network latency blocks the callback thread during POST. We use timeout=5 (`keylogger.py:249`) to avoid hanging indefinitely but 5 seconds is still noticeable if batches send frequently.

### Optimizations

What we did to make it faster:
- **Window title caching**: Only update every 0.5 seconds instead of every keystroke. Reduces API calls by 99%+ for typical typing speeds.
- **Batched webhook delivery**: Sending 50 events in one request instead of 50 individual requests reduces network overhead from ~1 second per keystroke to ~1 second per 50 keystrokes.
- **Lock-free reads in hot path**: The `_on_press` callback doesn't acquire locks during key processing, only when writing to shared resources. Reduces contention.

### Scalability

**Vertical scaling:**
Adding CPU/RAM helps with faster file I/O and larger webhook batches. Disk speed matters more than CPU since we're I/O bound. 16GB RAM is overkill, this runs fine on 512MB.

**Horizontal scaling:**
Doesn't apply. This runs on a single victim machine. You can't distribute one keylogger across multiple hosts (though you could deploy copies to multiple victims).

## Design Decisions

### Decision 1: Plain Text Logs vs Encrypted Logs

**What we chose:**
Plain text logs written with Python's logging module.

**Alternatives considered:**
- Encrypted logs with AES: Harder to detect via keyword scans but requires key management and decryption on exfiltration
- Database storage (SQLite): Enables querying and indexing but adds dependency and creates obvious .db file

**Trade-offs:**
Plain text is simple and educational. You can open log files in any text editor and immediately see captured keystrokes. This trades stealth (forensics can easily find passwords in plaintext) for learning value. Production malware would encrypt logs.

### Decision 2: Dataclasses vs Regular Classes

**What we chose:**
Dataclasses for KeyEvent and KeyloggerConfig.

**Alternatives considered:**
- Regular classes with manual `__init__`: More flexible but verbose
- Named tuples: Immutable and simple but no type hints or default values
- dictionaries: Most flexible but no type safety

**Trade-offs:**
Dataclasses give us type hints, default values, automatic `__repr__`, and less boilerplate. This makes the code self-documenting and safer. We give up some flexibility (can't dynamically add fields) but gain clarity.

### Decision 3: pynput vs Platform-Specific Hooks

**What we chose:**
pynput library for cross-platform keyboard capture.

**Alternatives considered:**
- SetWindowsHookEx on Windows: Lower level, harder to detect, but Windows-only
- Quartz Events on macOS: More control, requires elevated permissions
- X11 XRecord on Linux: Works on older systems, doesn't support Wayland

**Trade-offs:**
pynput abstracts platform differences and requires minimal code. We give up some control and performance (pynput adds overhead) but gain portability. Single codebase runs on all major platforms.

## Deployment Architecture

This is a standalone Python script, not a service. Deployment depends on attack scenario:

**Persistence (Windows):**
Registry Run key: `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`
Scheduled Task: `schtasks /create /tn "SystemUpdate" /tr "python keylogger.py" /sc onlogon`

**Persistence (macOS):**
LaunchAgent: `~/Library/LaunchAgents/com.example.keylogger.plist`

**Persistence (Linux):**
Systemd user service: `~/.config/systemd/user/keylogger.service`
Cron: `@reboot python /path/to/keylogger.py`

Deployment requires initial access (phishing, USB drop, etc) and depends on whether victim has Python installed or if you compile to executable with PyInstaller.

## Error Handling Strategy

### Error Types

1. **Import failures (missing dependencies)** - Caught at module level (`keylogger.py:35-54`), sets module to None. Code checks `if win32gui:` before using platform-specific features.
2. **Webhook delivery failures** - Caught and logged (`keylogger.py:251-255`), doesn't crash the keylogger. Events remain in buffer for next attempt.
3. **File I/O errors** - Not explicitly handled. Would crash on permission denied or disk full. Should wrap in try/except for production.

### Recovery Mechanisms

**Webhook failure scenario:**
- Detection: `requests.post()` raises exception or returns non-200 status
- Response: Log error message, leave events in buffer
- Recovery: Next keystroke batch includes failed events (retry)

**File rotation failure scenario:**
- Detection: `Path.stat()` or file open fails during rotation
- Response: Currently would crash with unhandled exception
- Recovery: Should log to stderr and continue with existing file

## Extensibility

### Where to Add Features

Want to add screenshot capture on certain keywords? Here's where it goes:

1. Create new `ScreenshotCapture` class in the service layer (similar to WebhookDelivery)
2. Modify `Keylogger._on_press()` to check for trigger keywords (`keylogger.py:367-383`)
3. Call `screenshot.capture()` when keyword detected (like "password" or "credit card")
4. Store screenshots alongside logs or bundle in webhook payload

Want to add clipboard monitoring?

1. Create `ClipboardMonitor` class that polls clipboard with `pyperclip`
2. Start monitoring thread in `Keylogger.start()` (`keylogger.py:395-413`)
3. Log clipboard changes to same LogManager instance

## Limitations

Current architectural limitations:
1. **Single-threaded event processing** - Keystrokes processed sequentially. Under extreme load (gaming, rapid macros), events could queue up. Fix: Process events in thread pool.
2. **No encryption** - Logs and webhooks use plaintext (HTTPS encrypts transport but payload is unencrypted JSON). Fix: Add AES encryption with key derivation.
3. **No persistence** - Program doesn't restart after reboot. Fix: Add platform-specific autostart mechanisms.
4. **No stealth** - Process shows in task manager with obvious name "python keylogger.py". Fix: Compile to executable with PyInstaller and rename to "svchost.exe" or similar.

These are not bugs, they're conscious trade-offs to keep the educational project simple. Fixing them would require platform-specific code that obscures the core concepts.

## Comparison to Similar Systems

### Commercial Keyloggers (Spyrix, Revealer Keylogger)

How we're different:
- Commercial tools are compiled executables with obfuscation and anti-detection, we're readable Python source
- They include screenshot capture, webcam access, and full system monitoring, we focus on keystroke capture
- They use kernel drivers for stealth, we use user-space libraries that are easier to detect

Why we made different choices:
This is an educational project to teach concepts, not production malware. Readable source code and simple architecture help learning. Commercial tools prioritize stealth, we prioritize clarity.

### Open Source Alternatives (PyLogger, Python-Keylogger)

How we're different:
- Many open source keyloggers lack tests, we include `test_keylogger.py` with component tests
- We use modern Python (dataclasses, type hints, enum) instead of legacy Python 2 code
- Our architecture separates concerns (LogManager, WebhookDelivery) instead of monolithic main function

Why we made different choices:
Clean architecture makes the code easier to understand and extend. Type hints catch bugs early. Tests verify components work correctly.

## Key Files Reference

Quick map of where to find things:

- `keylogger.py:64-82` - KeyloggerConfig dataclass (all configuration options)
- `keylogger.py:84-107` - KeyEvent dataclass (event structure)
- `keylogger.py:121-165` - WindowTracker class (platform-specific window detection)
- `keylogger.py:168-218` - LogManager class (file writing and rotation)
- `keylogger.py:221-263` - WebhookDelivery class (remote exfiltration)
- `keylogger.py:293-424` - Keylogger main class (orchestration)
- `keylogger.py:322-351` - _process_key() method (key classification)
- `keylogger.py:367-383` - _on_press() callback (event handler)
- `test_keylogger.py` - Component tests for verification

## Next Steps

Now that you understand the architecture:
1. Read [03-IMPLEMENTATION.md](./03-IMPLEMENTATION.md) for detailed code walkthrough and implementation patterns
2. Try modifying WindowTracker to cache window titles longer (10 seconds instead of 0.5) and observe the performance impact
3. Experiment with changing `webhook_batch_size` from 50 to 5 and monitor network traffic to see the difference in request frequency
