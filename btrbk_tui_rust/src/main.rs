use chrono::{Local, NaiveDate, NaiveDateTime};
use ncurses::*;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::fs;
use std::ffi::{CStr, OsStr};
use std::io::Read;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::rc::Rc;
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

/// btrbk configuration, read to discover the backup target
const BTRBK_CONF: &str = "/etc/btrbk/btrbk.conf";

const VERSION: &str = env!("CARGO_PKG_VERSION");

const KEY_ESC: i32 = 27;
const KEY_LF: i32 = 10;
const KEY_CR: i32 = 13;
const KEY_SPACE: i32 = 32;
const KEY_DEL: i32 = 127;
const KEY_BS: i32 = 8;

// Durata dei messaggi di stato
const STATUS_SHORT: Duration = Duration::from_secs(3);
const STATUS_MEDIUM: Duration = Duration::from_secs(5);
const STATUS_LONG: Duration = Duration::from_secs(10);
const STATUS_RESULT: Duration = Duration::from_secs(15);
const STATUS_CRITICAL: Duration = Duration::from_secs(60);

/// Righe di output di btrbk tenute in memoria durante la creazione degli snapshot.
const MAX_OUTPUT_LINES: usize = 1000;

/// Voci della schermata Settings, nell'ordine in cui sono mostrate.
const SETTINGS: [&str; 5] = [
    "BTR Pool Directory",
    "Snapshots Directory",
    "Auto Cleanup .BROKEN",
    "Confirm Actions",
    "Show Timestamps",
];

#[derive(Serialize, Deserialize, Clone)]
#[serde(default)]
struct Config {
    btr_pool_dir: String,
    snapshots_dir: String,
    auto_cleanup: bool,
    confirm_actions: bool,
    show_timestamps: bool,
    theme: String,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            btr_pool_dir: "/mnt/btr_pool".to_string(),
            snapshots_dir: "/mnt/btr_pool/btrbk_snapshots".to_string(),
            auto_cleanup: false,
            confirm_actions: true,
            show_timestamps: true,
            theme: "default".to_string(),
        }
    }
}

/// Snapshot raggruppati per subvolume ("@" per primo, poi in ordine alfabetico);
/// dentro ogni gruppo il più recente è in testa.
type SnapshotGroups = Vec<(String, Vec<String>)>;

/// Snapshots of one subvolume, oldest first, each with its UUID (None when it
/// could not be read).
type PurgeCandidates = (String, Vec<(String, Option<String>)>);

/// Esito di un'operazione di restore. Le varianti di errore portano il motivo.
enum RestoreOutcome {
    /// Restore completato e verificato.
    Success,
    /// Restore fallito ma rollback riuscito: il sistema è nello stato precedente.
    Failed(String),
    /// Restore fallito E rollback fallito: stato incoerente, intervento manuale necessario.
    RollbackFailed(String),
}

/// What a purge would delete, computed before touching anything.
#[derive(Debug, Default, PartialEq)]
struct PurgePlan {
    /// Snapshot names to delete, oldest first.
    delete: Vec<String>,
    /// Subvolumes left alone because they share no snapshot with the target.
    skipped: Vec<String>,
}

/// Where btrbk sends its backups, as far as ssh is concerned.
#[derive(Debug, PartialEq)]
struct SshTarget {
    host: String,
    port: Option<String>,
    path: String,
    user: Option<String>,
    identity: Option<String>,
}

#[derive(Clone, Copy, PartialEq)]
enum Screen {
    Main,
    Settings,
}

struct App {
    config: Config,
    /// Where the configuration is saved.
    config_path: PathBuf,
    /// Where it was read from, when that is somewhere else (an old location).
    config_read_from: Option<PathBuf>,
    /// uid/gid to give a config written as root into the invoking user's home.
    config_owner: Option<(u32, u32)>,
    screen: Screen,
    selected_row: usize,
    selected_col: usize,
    status_message: String,
    status_until: Option<Instant>,
    reboot_needed: bool,
    // Cache degli snapshot: evita di rileggere il filesystem ad ogni frame.
    // None = cache invalidata (verrà ricalcolata al prossimo accesso).
    snapshots_cache: Option<Rc<SnapshotGroups>>,
}

impl App {
    /// `config_file` is the --config option; without it the file is looked up
    /// with `config_candidates`.
    fn new(config_file: Option<PathBuf>) -> Self {
        let invoking = invoking_user();
        let (config_path, read_from) = match config_file {
            Some(path) => (path, None),
            None => {
                let candidates = config_candidates(
                    invoking.as_ref().map(|user| user.home.as_path()),
                    dirs::home_dir().as_deref(),
                );
                let found = candidates.iter().find(|path| path.is_file()).cloned();
                // An old location is read but never written: saving moves the
                // settings to the current name
                match found {
                    Some(path) if !is_legacy_config(&path) => (path, None),
                    other => (candidates[0].clone(), other),
                }
            }
        };
        let config_owner = invoking
            .filter(|user| config_path.starts_with(&user.home))
            .map(|user| (user.uid, user.gid));

        let mut app = App {
            config: Config::default(),
            config_read_from: read_from,
            config_owner,
            config_path,
            screen: Screen::Main,
            selected_row: 0,
            selected_col: 0,
            status_message: String::new(),
            status_until: None,
            reboot_needed: false,
            snapshots_cache: None,
        };

        app.load_config();
        app
    }

    /// Restituisce gli snapshot dalla cache, ricalcolandoli solo se invalidata.
    fn snapshots_cached(&mut self) -> Rc<SnapshotGroups> {
        if let Some(cached) = &self.snapshots_cache {
            return Rc::clone(cached);
        }
        let fresh = Rc::new(self.get_snapshots());
        self.snapshots_cache = Some(Rc::clone(&fresh));
        fresh
    }

    /// Invalida la cache: il prossimo accesso rileggerà il filesystem.
    fn invalidate_snapshots(&mut self) {
        self.snapshots_cache = None;
    }

    fn load_config(&mut self) {
        let source = self.config_read_from.as_ref().unwrap_or(&self.config_path);
        if let Ok(content) = fs::read_to_string(source)
            && let Ok(saved_config) = serde_json::from_str::<Config>(&content)
        {
            self.config = saved_config;
        }
    }

    fn save_config(&mut self) -> bool {
        let Ok(json) = serde_json::to_string_pretty(&self.config) else {
            return false;
        };

        // Directories about to be created, so that they can be handed over too
        let mut created = Vec::new();
        let mut dir = self.config_path.parent();
        while let Some(path) = dir.filter(|path| !path.exists()) {
            created.push(path.to_path_buf());
            dir = path.parent();
        }
        if let Some(parent) = self.config_path.parent()
            && fs::create_dir_all(parent).is_err()
        {
            return false;
        }
        if fs::write(&self.config_path, json).is_err() {
            return false;
        }

        // Written as root into the invoking user's home: give it back to them,
        // or their next edit of their own file would need root
        if let Some((uid, gid)) = self.config_owner {
            for path in created.iter().chain(std::iter::once(&self.config_path)) {
                let _ = std::os::unix::fs::chown(path, Some(uid), Some(gid));
            }
        }
        self.config_read_from = None;
        true
    }

    fn get_snapshots(&self) -> SnapshotGroups {
        let Ok(entries) = fs::read_dir(&self.config.snapshots_dir) else {
            return Vec::new();
        };
        let names = entries
            .flatten()
            .filter(|entry| entry.path().is_dir())
            .map(|entry| entry.file_name().to_string_lossy().into_owned());
        group_snapshots(names)
    }

    fn format_snapshot_name(&self, snapshot: &str) -> String {
        if self.config.show_timestamps
            && let Some((_, timestamp)) = split_snapshot_name(snapshot)
            && let Some(dt) = parse_btrbk_timestamp(timestamp)
        {
            return format!("{} ({})", snapshot, dt.format("%Y-%m-%d %H:%M:%S"));
        }
        snapshot.to_string()
    }

    fn init_colors(&self) {
        start_color();
        use_default_colors();

        init_pair(1, COLOR_BLACK, COLOR_CYAN); // Selected item
        init_pair(2, COLOR_RED, -1); // Headers
        init_pair(3, COLOR_GREEN, -1); // Success
        init_pair(4, COLOR_YELLOW, -1); // Warning
        init_pair(5, COLOR_WHITE, COLOR_BLACK); // Status bar
        init_pair(6, COLOR_CYAN, -1); // Info
    }

    fn set_status(&mut self, message: &str, duration: Duration) {
        self.status_message = message.to_string();
        self.status_until = Some(Instant::now() + duration);
    }

    /// Mostra subito un messaggio prima di un'operazione che blocca l'interfaccia.
    /// `set_status` da solo non basta: il messaggio verrebbe disegnato solo al
    /// frame successivo, cioè a operazione già conclusa.
    fn show_busy(&mut self, message: &str) {
        self.set_status(message, STATUS_SHORT);
        self.draw_screen();
        refresh();
    }

    /// Runs `btrbk run --progress`, streaming its output to the screen.
    fn create_snapshot(&self) -> Result<(), String> {
        let (height, _) = get_max_yx();

        erase();
        self.draw_header();

        let title = "Creating Snapshots with btrbk...";
        attron(COLOR_PAIR(2) | A_BOLD());
        put_centered(4, title);
        attroff(COLOR_PAIR(2) | A_BOLD());

        attron(A_DIM());
        put_centered(6, "Press ESC to cancel or wait for completion");
        attroff(A_DIM());

        // Simple output area - only horizontal borders
        let output_start_y = 8;
        let output_height = (height - 12).max(1);
        put_separator(output_start_y - 1);
        put_separator(output_start_y + output_height);
        refresh();

        let mut command = Command::new("btrbk");
        command
            .args(["run", "--progress"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // Own session: no controlling terminal, so a stray ssh password prompt
        // fails instead of scribbling over the curses screen, and the whole
        // process tree (btrfs send, ssh, pv) can be signalled at once on cancel.
        // SAFETY: setsid() is async-signal-safe and touches no shared state.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }

        let mut process = command
            .spawn()
            .map_err(|err| format!("cannot run btrbk: {}", err))?;

        let (Some(stdout), Some(stderr)) = (process.stdout.take(), process.stderr.take()) else {
            let _ = process.kill();
            let _ = process.wait();
            return Err("failed to capture btrbk output".to_string());
        };

        // I thread terminano da soli quando le pipe si chiudono
        let (tx, rx) = mpsc::channel();
        let tx_stderr = tx.clone();
        thread::spawn(move || forward_stream(stdout, &tx));
        thread::spawn(move || forward_stream(stderr, &tx_stderr));

        let group = -(process.id() as libc::pid_t);
        let mut log = OutputLog::default();
        let mut cancelled_at: Option<Instant> = None;
        let mut exited_at: Option<Instant> = None;
        let mut killed = false;

        timeout(50);
        loop {
            if getch() == KEY_ESC && cancelled_at.is_none() {
                // SIGINT to the whole group is what Ctrl-C does in a shell, the
                // case btrbk is written for: it aborts and logs the transaction.
                unsafe { libc::kill(group, libc::SIGINT) };
                cancelled_at = Some(Instant::now());
                attron(COLOR_PAIR(4) | A_BOLD());
                put_centered(height - 2, "Cancelling, waiting for btrbk to stop...");
                attroff(COLOR_PAIR(4) | A_BOLD());
                refresh();
            }

            if let Some(since) = cancelled_at
                && !killed
                && since.elapsed() > Duration::from_secs(5)
            {
                unsafe { libc::kill(group, libc::SIGKILL) };
                killed = true;
            }

            let mut dirty = false;
            let mut disconnected = false;
            loop {
                match rx.try_recv() {
                    Ok((text, transient)) => {
                        dirty |= log.push(&text, transient);
                    }
                    Err(mpsc::TryRecvError::Empty) => break,
                    Err(mpsc::TryRecvError::Disconnected) => {
                        disconnected = true;
                        break;
                    }
                }
            }
            if dirty {
                render_output_area(&log.lines, output_start_y, output_height);
                refresh();
            }
            // Si esce solo a btrbk terminato, così l'escalation a SIGKILL qui
            // sopra resta attiva finché serve. Se qualcuno tiene ancora aperte
            // le pipe dopo la sua uscita non lo si aspetta all'infinito.
            if exited_at.is_none() && matches!(process.try_wait(), Ok(Some(_))) {
                exited_at = Some(Instant::now());
            }
            if let Some(since) = exited_at
                && (disconnected || (!dirty && since.elapsed() > Duration::from_millis(500)))
            {
                break;
            }
        }

        let succeeded = process.wait().map(|status| status.success()).unwrap_or(false);
        timeout(100);

        if cancelled_at.is_some() {
            // btrbk has cleaned up and gone: whatever ignored SIGINT must not
            // outlive the cancel as an orphan running as root
            unsafe { libc::kill(group, libc::SIGKILL) };
            return Err("cancelled by user".to_string());
        }

        // ASCII only: under pkexec the locale is C and glyphs would be garbled
        let (pair, message) = if succeeded {
            (3, "[OK] Snapshots created successfully! Press any key to continue...")
        } else {
            (4, "[FAILED] Error creating snapshots! Press any key to continue...")
        };
        attron(COLOR_PAIR(pair) | A_BOLD());
        put_centered(height - 2, message);
        attroff(COLOR_PAIR(pair) | A_BOLD());
        refresh();

        timeout(-1);
        getch();
        timeout(100);

        if succeeded {
            Ok(())
        } else {
            Err(log
                .lines
                .last()
                .cloned()
                .unwrap_or_else(|| "btrbk exited with an error".to_string()))
        }
    }

    /// Works out which old snapshots can go, keeping the ones the backup
    /// target still needs.
    ///
    /// btrbk does not protect snapshots that serve as parent for incremental
    /// backups (see btrbk.conf(5)), so deleting the newest snapshot the target
    /// also holds forces the next run into a full send — for a large subvolume
    /// that means hours of transfer. We therefore keep the newest snapshot
    /// present on the target *and* everything after it.
    ///
    /// Fails when the target cannot be queried: the caller must then refuse to
    /// purge, since it cannot tell which snapshot is still needed.
    fn plan_purge(&self) -> Result<PurgePlan, String> {
        let target_uuids = target_received_uuids()?;

        let entries = fs::read_dir(&self.config.snapshots_dir)
            .map_err(|err| format!("cannot read snapshots directory: {}", err))?;
        let names = entries
            .flatten()
            .filter(|entry| entry.path().is_dir())
            .map(|entry| entry.file_name().to_string_lossy().into_owned());

        let snapshots_dir = Path::new(&self.config.snapshots_dir);
        let groups: Vec<PurgeCandidates> = group_snapshots(names)
            .into_iter()
            .map(|(prefix, mut snapshots)| {
                snapshots.reverse(); // oldest first
                let with_uuid = snapshots
                    .into_iter()
                    .map(|name| {
                        let uuid = local_subvolume_uuid(&snapshots_dir.join(&name));
                        (name, uuid)
                    })
                    .collect();
                (prefix, with_uuid)
            })
            .collect();

        Ok(compute_purge_plan(&groups, &target_uuids))
    }

    /// Deletes the planned snapshots. Returns (deleted, failed).
    fn execute_purge(&self, plan: &PurgePlan) -> (usize, usize) {
        let snapshots_dir = Path::new(&self.config.snapshots_dir);
        let mut deleted = 0;
        for name in &plan.delete {
            let path = snapshots_dir.join(name);
            if run_command(&["btrfs", "subvolume", "delete", &path.to_string_lossy()]).is_ok() {
                deleted += 1;
            }
        }
        (deleted, plan.delete.len() - deleted)
    }

    /// Deletes every .BROKEN subvolume in the pool. Returns (deleted, failed).
    fn clean_broken_subvolumes(&self) -> Result<(usize, usize), String> {
        let entries = fs::read_dir(&self.config.btr_pool_dir)
            .map_err(|err| format!("cannot read pool directory: {}", err))?;

        let broken: Vec<PathBuf> = entries
            .flatten()
            .map(|entry| entry.path())
            .filter(|path| {
                path.is_dir()
                    && path
                        .file_name()
                        .is_some_and(|name| name.to_string_lossy().contains(".BROKEN"))
            })
            .collect();

        let mut deleted = 0;
        for path in &broken {
            if run_command(&["btrfs", "subvolume", "delete", &path.to_string_lossy()]).is_ok() {
                deleted += 1;
            }
        }
        Ok((deleted, broken.len() - deleted))
    }

    fn draw_header(&self) {
        let (_, width) = get_max_yx();

        let title = format!("BTRBK TUI v{}", VERSION);
        attron(COLOR_PAIR(5) | A_BOLD());
        put(0, 0, &format!("{:^width$}", title, width = width.max(0) as usize));
        attroff(COLOR_PAIR(5) | A_BOLD());

        put_separator(1);
    }

    fn draw_footer(&self) {
        let (height, _) = get_max_yx();

        let footer_text = match self.screen {
            Screen::Main => {
                let mut keys = vec![
                    "Up/Down: Navigate",
                    "Left/Right: Switch",
                    "ENTER: Restore",
                    "S: Settings",
                    "R: Refresh",
                    "I: Snapshot",
                    "P: Purge OLD",
                    "B: Clean BROKEN",
                ];
                if self.reboot_needed {
                    keys.push("H: REBOOT");
                }
                keys.push("Q: Quit");
                keys.join(" | ")
            }
            Screen::Settings => {
                "Up/Down: Navigate | ENTER: Edit | SPACE: Toggle | S: Save | ESC: Back | Q: Quit"
                    .to_string()
            }
        };

        put_separator(height - 2);
        attron(COLOR_PAIR(5));
        put(height - 1, 0, &footer_text);
        attroff(COLOR_PAIR(5));
    }

    fn draw_status(&mut self) {
        let (height, _) = get_max_yx();

        if self.status_until.is_some_and(|until| Instant::now() < until) {
            attron(COLOR_PAIR(6));
            put(height - 3, 0, &self.status_message);
            attroff(COLOR_PAIR(6));
            return;
        }

        self.status_message.clear();
        self.status_until = None;

        // Show reboot warning only when no temporary messages are active
        if self.reboot_needed {
            attron(COLOR_PAIR(4) | A_BOLD());
            put(height - 3, 0, "WARNING: REBOOT REQUIRED - Press H to reboot system");
            attroff(COLOR_PAIR(4) | A_BOLD());
        }
    }

    /// Riporta la selezione dentro i limiti: le liste cambiano dopo refresh,
    /// purge e restore, e una selezione fuori lista sarebbe invisibile.
    fn clamp_selection(&mut self, groups: &SnapshotGroups) {
        self.selected_col = self.selected_col.min(groups.len().saturating_sub(1));
        let rows = groups.get(self.selected_col).map_or(0, |(_, s)| s.len());
        self.selected_row = self.selected_row.min(rows.saturating_sub(1));
    }

    fn draw_main_screen(&mut self) {
        let (height, width) = get_max_yx();
        let groups = self.snapshots_cached();

        // Show current configuration
        let config_info = format!(
            "Pool: {} | Snapshots: {}",
            self.config.btr_pool_dir, self.config.snapshots_dir
        );
        attron(A_DIM());
        put(2, 2, &config_info);
        attroff(A_DIM());

        if groups.is_empty() {
            attron(COLOR_PAIR(4) | A_BOLD());
            put_centered(height / 2, "No snapshots found!");
            attroff(COLOR_PAIR(4) | A_BOLD());
            attron(A_DIM());
            put_centered(height / 2 + 1, "S: check the paths | I: create snapshots");
            attroff(A_DIM());
            return;
        }

        self.clamp_selection(&groups);

        // Calculate column positions dynamically
        let col_width = ((width - 4) / groups.len() as i32).max(1);
        let text_width = (col_width - 2).max(1) as usize;
        let start_y = 4;
        // Rows from start_y down to the line above the status bar; the row in
        // between is kept for the "more below" indicator
        let visible = (height - 4 - start_y - 1).max(1) as usize;

        for (col_idx, (prefix, snapshots)) in groups.iter().enumerate() {
            let col_x = 2 + (col_idx as i32) * col_width;

            let header = format!("{} ({})", prefix.to_uppercase(), snapshots.len());
            attron(COLOR_PAIR(2) | A_BOLD());
            put(start_y - 1, col_x, &truncate_str(&header, text_width));
            attroff(COLOR_PAIR(2) | A_BOLD());

            // Solo la colonna selezionata scorre, quanto basta a tenere visibile il cursore
            let first = if col_idx == self.selected_col {
                (self.selected_row + 1).saturating_sub(visible)
            } else {
                0
            };

            for (row, snapshot) in snapshots.iter().enumerate().skip(first).take(visible) {
                let y = start_y + (row - first) as i32;
                let shown = truncate_str(&self.format_snapshot_name(snapshot), text_width);
                let selected = col_idx == self.selected_col && row == self.selected_row;
                if selected {
                    attron(COLOR_PAIR(1));
                }
                put(y, col_x, &shown);
                if selected {
                    attroff(COLOR_PAIR(1));
                }
            }

            if snapshots.len() > visible {
                let last = (first + visible).min(snapshots.len());
                let range = format!("[{}-{} of {}]", first + 1, last, snapshots.len());
                attron(A_DIM());
                put(start_y + visible as i32, col_x, &truncate_str(&range, text_width));
                attroff(A_DIM());
            }
        }
    }

    fn draw_settings_screen(&self) {
        let (height, _) = get_max_yx();
        let yes_no = |flag: bool| if flag { "Yes" } else { "No" };
        let values = [
            self.config.btr_pool_dir.as_str(),
            self.config.snapshots_dir.as_str(),
            yes_no(self.config.auto_cleanup),
            yes_no(self.config.confirm_actions),
            yes_no(self.config.show_timestamps),
        ];

        let start_y = 4;

        attron(COLOR_PAIR(2) | A_BOLD());
        put(start_y - 1, 4, "SETTINGS");
        attroff(COLOR_PAIR(2) | A_BOLD());

        for (i, (label, value)) in SETTINGS.iter().zip(values).enumerate() {
            let y = start_y + (i * 2) as i32;
            if y >= height - 6 {
                break;
            }

            if i == self.selected_row {
                attron(COLOR_PAIR(1));
            }
            put(y, 4, &format!("{}:", label));
            put(y + 1, 6, value);
            if i == self.selected_row {
                attroff(COLOR_PAIR(1));
            }
        }

        // Config file info
        let config_exists = if self.config_path.exists() { "EXISTS" } else { "NOT FOUND" };
        attron(A_DIM());
        put(
            height - 5,
            4,
            &format!("Config: {} ({})", self.config_path.display(), config_exists),
        );
        if let Some(old) = &self.config_read_from {
            put(
                height - 4,
                4,
                &format!("Read from the old location {}: saving moves it", old.display()),
            );
        }
        attroff(A_DIM());
    }

    fn draw_screen(&mut self) {
        // erase() e non clear(): clear() forza il ridisegno completo del
        // terminale ad ogni refresh, cioè sfarfallio a ogni frame
        erase();
        self.draw_header();
        match self.screen {
            Screen::Main => self.draw_main_screen(),
            Screen::Settings => self.draw_settings_screen(),
        }
        self.draw_status();
        self.draw_footer();
    }

    fn confirm_dialog(&self, message: &str) -> bool {
        if !self.config.confirm_actions {
            return true;
        }

        let (height, width) = get_max_yx();
        let width = width.max(0) as usize;
        let hint = "Y: Yes | N: No";
        let lines: Vec<&str> = message.lines().collect();
        let longest = lines.iter().map(|line| line.chars().count()).max().unwrap_or(0);
        let wanted = longest.max(hint.len()) + 6;
        let dialog_width = wanted.min(width.saturating_sub(4)).max(8);
        // border, message, blank, hint, border
        let dialog_height = lines.len() as i32 + 4;
        let dialog_y = height / 2 - dialog_height / 2;
        let dialog_x = (width.saturating_sub(dialog_width) / 2) as i32;
        let inner = dialog_width - 2;

        let border = format!("+{}+", "-".repeat(inner));
        let blank = format!("|{}|", " ".repeat(inner));
        attron(A_BOLD());
        put(dialog_y, dialog_x, &border);
        for i in 1..dialog_height - 1 {
            put(dialog_y + i, dialog_x, &blank);
        }
        put(dialog_y + dialog_height - 1, dialog_x, &border);
        attroff(A_BOLD());

        for (i, line) in lines.iter().enumerate() {
            put(dialog_y + 1 + i as i32, dialog_x + 3, &truncate_str(line, inner.saturating_sub(4)));
        }
        put(dialog_y + dialog_height - 2, dialog_x + 3, &truncate_str(hint, inner.saturating_sub(4)));
        refresh();

        timeout(-1);
        let answer = loop {
            match getch() {
                KEY_ESC => break false,
                key => match key_char(key) {
                    Some('y') => break true,
                    Some('n') => break false,
                    _ => {}
                },
            }
        };
        timeout(100);
        answer
    }

    /// Replaces the subvolume `subvol_name` (e.g. "@home") with a writable
    /// snapshot of `snapshot`, keeping the previous one as .BROKEN.
    fn restore_snapshot(&self, snapshot: &str, subvol_name: &str) -> RestoreOutcome {
        let source_path = Path::new(&self.config.snapshots_dir).join(snapshot);

        // Pre-check: lo snapshot sorgente deve esistere prima di toccare il subvolume corrente
        if !source_path.exists() {
            return RestoreOutcome::Failed(format!("{} no longer exists", source_path.display()));
        }

        let pool = Path::new(&self.config.btr_pool_dir);
        let current_subvol = pool.join(subvol_name);
        // Generate unique .BROKEN name with timestamp
        let timestamp = Local::now().format("%Y%m%d_%H%M%S");
        let broken_subvol = pool.join(format!("{}.BROKEN.{}", subvol_name, timestamp));

        let current_existed = current_subvol.exists();

        if current_existed {
            // Guardia: deve essere un vero subvolume btrfs prima di spostarlo
            // (evita di spostare/distruggere una directory normale per errore)
            if let Err(reason) =
                run_command(&["btrfs", "subvolume", "show", &current_subvol.to_string_lossy()])
            {
                return RestoreOutcome::Failed(format!(
                    "{} is not a btrfs subvolume: {}",
                    current_subvol.display(),
                    reason
                ));
            }

            // rename(2) would silently replace an empty directory
            if broken_subvol.exists() {
                return RestoreOutcome::Failed(format!("{} already exists", broken_subvol.display()));
            }

            // Move current to .BROKEN. rename(2) e non mv: tra filesystem
            // diversi fallisce, invece di mettersi a copiare un subvolume
            if let Err(err) = fs::rename(&current_subvol, &broken_subvol) {
                return RestoreOutcome::Failed(format!("cannot move {}: {}", subvol_name, err));
            }
        }

        // Rollback: rimette al suo posto il subvolume originale
        let roll_back = |reason: String| -> RestoreOutcome {
            if current_existed && let Err(err) = fs::rename(&broken_subvol, &current_subvol) {
                return RestoreOutcome::RollbackFailed(format!(
                    "{}; original kept as {} ({})",
                    reason,
                    broken_subvol.display(),
                    err
                ));
            }
            RestoreOutcome::Failed(reason)
        };

        // Create new snapshot
        if let Err(reason) = run_command(&[
            "btrfs",
            "subvolume",
            "snapshot",
            &source_path.to_string_lossy(),
            &current_subvol.to_string_lossy(),
        ]) {
            return roll_back(format!("snapshot failed: {}", reason));
        }

        // Verifica che il restore sia andato a buon fine
        if let Err(reason) = self.verify_restore_success(&current_subvol, subvol_name) {
            // Rollback completo: rimuovi il subvolume fallito e ripristina l'originale
            if let Err(err) =
                run_command(&["btrfs", "subvolume", "delete", &current_subvol.to_string_lossy()])
            {
                // Il subvolume fallito occupa ancora il path: impossibile ripristinare l'originale
                return RestoreOutcome::RollbackFailed(format!(
                    "{}; cannot remove the failed restore ({}), original kept as {}",
                    reason,
                    err,
                    broken_subvol.display()
                ));
            }
            return roll_back(reason);
        }

        // Auto cleanup if enabled - rimuovi .BROKEN solo se il restore è andato a buon fine
        if self.config.auto_cleanup && current_existed {
            let _ = run_command(&["btrfs", "subvolume", "delete", &broken_subvol.to_string_lossy()]);
        }

        RestoreOutcome::Success
    }

    fn verify_restore_success(&self, restored_subvol: &Path, subvol_name: &str) -> Result<(), String> {
        // 1. Verifica che il subvolume esista
        if !restored_subvol.exists() {
            return Err("restored subvolume is missing".to_string());
        }

        // 2. Verifica che sia un subvolume btrfs valido
        run_command(&["btrfs", "subvolume", "show", &restored_subvol.to_string_lossy()])
            .map_err(|reason| format!("restored path is not a subvolume: {}", reason))?;

        // 3. Verifica file/directory critici in base al subvolume. Root e home
        // si riconoscono dal nome "@"/"@home" o da ciò che è montato su / e
        // /home: nei layout senza "@" si chiamano root, rootfs, home...
        let mountinfo = fs::read_to_string("/proc/self/mountinfo").unwrap_or_default();
        let mounted_at = |mountpoint: &str| mounted_subvolume(&mountinfo, mountpoint);
        let kind = if subvol_name == "@" || mounted_at("/").as_deref() == Some(subvol_name) {
            "root"
        } else if subvol_name == "@home" || mounted_at("/home").as_deref() == Some(subvol_name) {
            "home"
        } else {
            "other"
        };
        match kind {
            "root" => {
                for dir in ["etc", "usr", "var", "bin"] {
                    if !restored_subvol.join(dir).exists() {
                        return Err(format!("restored root has no /{}", dir));
                    }
                }
                for file in ["etc/fstab", "etc/passwd"] {
                    if !restored_subvol.join(file).is_file() {
                        return Err(format!("restored root has no /{}", file));
                    }
                }
            }
            "home" => {
                let mut entries = fs::read_dir(restored_subvol)
                    .map_err(|err| format!("restored home is unreadable: {}", err))?;
                if entries.next().is_none() {
                    return Err("restored home is empty".to_string());
                }
            }
            _ => {
                // Per qualsiasi altro subvolume (@games, @work, @custom, ecc.):
                // verifica solo che sia leggibile
                fs::read_dir(restored_subvol)
                    .map_err(|err| format!("restored subvolume is unreadable: {}", err))?;
            }
        }

        Ok(())
    }

    fn handle_main_input(&mut self, key: i32) {
        let groups = self.snapshots_cached();
        self.clamp_selection(&groups);

        match key {
            KEY_UP => self.selected_row = self.selected_row.saturating_sub(1),
            KEY_DOWN => {
                self.selected_row += 1;
                self.clamp_selection(&groups);
            }
            KEY_LEFT => {
                self.selected_col = self.selected_col.saturating_sub(1);
                self.clamp_selection(&groups);
            }
            KEY_RIGHT => {
                self.selected_col += 1;
                self.clamp_selection(&groups);
            }
            KEY_HOME => self.selected_row = 0,
            KEY_END => {
                self.selected_row = usize::MAX;
                self.clamp_selection(&groups);
            }
            KEY_LF | KEY_CR | KEY_ENTER => self.handle_snapshot_selection(&groups),
            _ => match key_char(key) {
                Some('s') => {
                    self.screen = Screen::Settings;
                    self.selected_row = 0;
                }
                Some('r') => {
                    self.invalidate_snapshots();
                    self.set_status("Snapshots refreshed", STATUS_SHORT);
                }
                Some('h') => self.handle_reboot(),
                Some('p') => self.handle_purge(),
                Some('b') => self.handle_clean_broken(),
                Some('i') => self.handle_create_snapshot(),
                _ => {}
            },
        }
    }

    fn handle_reboot(&mut self) {
        if !self.reboot_needed {
            self.set_status("No reboot needed", STATUS_SHORT);
        } else if self.confirm_dialog("Reboot system now?") {
            let _ = run_command(&["sync"]);
            if let Err(reason) = run_command(&["reboot"]) {
                self.set_status(&format!("Error: reboot failed: {}", reason), STATUS_LONG);
            }
        } else {
            self.set_status("Reboot cancelled", STATUS_SHORT);
        }
    }

    fn handle_purge(&mut self) {
        // querying the target over ssh takes a moment: say so before blocking
        self.show_busy("Checking backup target...");

        let plan = match self.plan_purge() {
            Ok(plan) => plan,
            Err(reason) => {
                self.set_status(
                    &format!("Nothing purged (chain left intact): {}", reason),
                    STATUS_RESULT,
                );
                return;
            }
        };

        let skipped = if plan.skipped.is_empty() {
            String::new()
        } else {
            format!(" ({} skipped: not on the backup target)", plan.skipped.join(", "))
        };

        if plan.delete.is_empty() {
            self.set_status(&format!("No old snapshots to purge{}", skipped), STATUS_LONG);
            return;
        }

        self.draw_screen();
        let question = format!(
            "Delete {} old snapshots? The backup chain is kept.",
            plan.delete.len()
        );
        if !self.confirm_dialog(&question) {
            self.set_status("Purge cancelled", STATUS_SHORT);
            return;
        }

        self.show_busy("Purging old snapshots...");
        let (deleted, failed) = self.execute_purge(&plan);
        self.invalidate_snapshots();

        if failed == 0 {
            self.set_status(&format!("Purged {} old snapshots{}", deleted, skipped), STATUS_RESULT);
        } else {
            self.set_status(
                &format!("Purged {} old snapshots, {} could NOT be deleted{}", deleted, failed, skipped),
                STATUS_RESULT,
            );
        }
    }

    fn handle_clean_broken(&mut self) {
        if !self.confirm_dialog("Delete all .BROKEN subvolumes?") {
            self.set_status("Clean cancelled", STATUS_SHORT);
            return;
        }

        self.show_busy("Cleaning .BROKEN subvolumes...");
        match self.clean_broken_subvolumes() {
            Err(reason) => self.set_status(&format!("Error: {}", reason), STATUS_LONG),
            Ok((0, 0)) => self.set_status("No .BROKEN subvolumes found", STATUS_MEDIUM),
            Ok((deleted, 0)) => {
                self.set_status(&format!("Cleaned {} .BROKEN subvolumes", deleted), STATUS_RESULT);
            }
            Ok((deleted, failed)) => self.set_status(
                &format!(
                    "Cleaned {} .BROKEN subvolumes, {} could NOT be deleted (still mounted?)",
                    deleted, failed
                ),
                STATUS_RESULT,
            ),
        }
    }

    fn handle_create_snapshot(&mut self) {
        if !self.confirm_dialog("Create new snapshots with btrbk?") {
            self.set_status("Snapshot creation cancelled", STATUS_SHORT);
            return;
        }

        let result = self.create_snapshot();
        // anche un run interrotto può aver già creato degli snapshot
        self.invalidate_snapshots();
        match result {
            Ok(()) => self.set_status("Snapshots created successfully", STATUS_LONG),
            Err(reason) => {
                self.set_status(&format!("Snapshot creation failed: {}", reason), STATUS_RESULT);
            }
        }
    }

    fn handle_snapshot_selection(&mut self, groups: &SnapshotGroups) {
        let Some((subvol_name, snapshots)) = groups.get(self.selected_col) else {
            return;
        };
        let Some(snapshot) = snapshots.get(self.selected_row) else {
            return;
        };

        // Il subvolume da sostituire è il prefisso dello snapshot, così com'è:
        // "@" -> @, "@home" -> @home, "@root" -> @root (mai confuso con "@").
        // Il dialogo dice quale path verrà toccato: con un pool sbagliato nei
        // settings sarebbe creato un subvolume nuovo invece di sostituirlo
        let target = Path::new(&self.config.btr_pool_dir).join(subvol_name);
        let effect = if target.exists() {
            format!("Replaces {} (the old one is kept as .BROKEN)", target.display())
        } else {
            format!("WARNING: {} does not exist, it will be CREATED", target.display())
        };
        if !self.confirm_dialog(&format!("Restore {} from {}?\n{}", subvol_name, snapshot, effect)) {
            self.set_status("Restore cancelled", STATUS_SHORT);
            return;
        }

        self.show_busy(&format!("Restoring {}...", subvol_name));

        match self.restore_snapshot(snapshot, subvol_name) {
            RestoreOutcome::Success => {
                self.reboot_needed = true;
                self.set_status(
                    &format!("{} restored! Press H to reboot when ready", subvol_name),
                    STATUS_RESULT,
                );
            }
            RestoreOutcome::Failed(reason) => {
                self.set_status(
                    &format!("Error: {} restore failed, rolled back: {}", subvol_name, reason),
                    STATUS_RESULT,
                );
            }
            RestoreOutcome::RollbackFailed(reason) => {
                self.set_status(
                    &format!(
                        "CRITICAL: {} restore AND rollback failed, manual recovery needed: {}",
                        subvol_name, reason
                    ),
                    STATUS_CRITICAL,
                );
            }
        }
        self.invalidate_snapshots();
    }

    fn handle_settings_input(&mut self, key: i32) {
        match key {
            KEY_UP => self.selected_row = self.selected_row.saturating_sub(1),
            KEY_DOWN => self.selected_row = (self.selected_row + 1).min(SETTINGS.len() - 1),
            KEY_LF | KEY_CR | KEY_ENTER => self.edit_setting(),
            KEY_SPACE => self.toggle_setting(),
            KEY_ESC => {
                self.screen = Screen::Main;
                self.selected_row = 0;
            }
            _ => {
                if key_char(key) == Some('s') {
                    if self.save_config() {
                        self.set_status("Configuration saved", STATUS_MEDIUM);
                    } else {
                        self.set_status("Error: failed to save configuration", STATUS_LONG);
                    }
                }
            }
        }
    }

    fn edit_setting(&mut self) {
        if self.selected_row > 1 {
            self.toggle_setting();
            return;
        }

        let (height, width) = get_max_yx();
        let field_name = if self.selected_row == 0 { "btr_pool_dir" } else { "snapshots_dir" };
        let current_value = if self.selected_row == 0 {
            self.config.btr_pool_dir.clone()
        } else {
            self.config.snapshots_dir.clone()
        };

        // Clear area for input
        let blank = " ".repeat((width - 8).max(0) as usize);
        for i in 0..5 {
            put(height / 2 - 2 + i, 4, &blank);
        }

        let input_y = height / 2 + 1;
        let input_x = 9;
        let input_width = (width - input_x - 4).max(1) as usize;
        put(height / 2 - 1, 4, &format!("Edit {}: ", field_name));
        put(height / 2, 4, &format!("Current: {}", current_value));
        put(input_y, 4, "New: ");
        put(height / 2 + 3, 4, "Press ENTER to confirm, ESC to cancel");

        curs_set(CURSOR_VISIBILITY::CURSOR_VISIBLE);
        timeout(-1);

        let mut input = String::new();
        let confirmed = loop {
            // Se il testo non ci sta si mostra la coda, dove si sta scrivendo
            let tail: String = {
                let count = input.chars().count();
                input.chars().skip(count.saturating_sub(input_width)).collect()
            };
            put(input_y, input_x, &" ".repeat(input_width));
            put(input_y, input_x, &tail);
            mv(input_y, input_x + tail.chars().count() as i32);
            refresh();

            match getch() {
                KEY_LF | KEY_CR | KEY_ENTER => break true,
                KEY_ESC => break false,
                KEY_BACKSPACE | KEY_DEL | KEY_BS => {
                    input.pop();
                }
                ch if (32..127).contains(&ch) => input.push(ch as u8 as char),
                _ => {}
            }
        };

        timeout(100);
        curs_set(CURSOR_VISIBILITY::CURSOR_INVISIBLE);

        let new_path = input.trim().to_string();
        if !confirmed || new_path.is_empty() {
            self.set_status("Edit cancelled", STATUS_SHORT);
            return;
        }

        let exists = Path::new(&new_path).is_dir();
        if self.selected_row == 0 {
            self.config.btr_pool_dir = new_path;
        } else {
            self.config.snapshots_dir = new_path;
        }
        self.invalidate_snapshots();

        if !self.save_config() {
            self.set_status(&format!("Updated {} but could NOT save the config file", field_name), STATUS_LONG);
        } else if exists {
            self.set_status(&format!("Updated {}", field_name), STATUS_MEDIUM);
        } else {
            self.set_status(
                &format!("Updated {} (WARNING: path does not exist)", field_name),
                STATUS_LONG,
            );
        }
    }

    fn toggle_setting(&mut self) {
        let (name, toggled) = match self.selected_row {
            2 => {
                self.config.auto_cleanup = !self.config.auto_cleanup;
                ("Auto cleanup", self.config.auto_cleanup)
            }
            3 => {
                self.config.confirm_actions = !self.config.confirm_actions;
                ("Confirm actions", self.config.confirm_actions)
            }
            4 => {
                self.config.show_timestamps = !self.config.show_timestamps;
                ("Show timestamps", self.config.show_timestamps)
            }
            _ => return,
        };
        let state = if toggled { "Yes" } else { "No" };
        if self.save_config() {
            self.set_status(&format!("{}: {}", name, state), STATUS_MEDIUM);
        } else {
            self.set_status(&format!("{}: {} (could NOT save the config file)", name, state), STATUS_LONG);
        }
    }

    fn run(&mut self) {
        curs_set(CURSOR_VISIBILITY::CURSOR_INVISIBLE);
        timeout(100);
        self.init_colors();

        loop {
            self.draw_screen();
            refresh();

            let key = getch();
            if key == ERR {
                continue;
            }
            if key_char(key) == Some('q') {
                break;
            }
            match self.screen {
                Screen::Main => self.handle_main_input(key),
                Screen::Settings => self.handle_settings_input(key),
            }
        }
    }
}

/// btrbk output as shown on screen. Progress meters rewrite their line with
/// '\r': such a line is transient and the next one takes its place.
#[derive(Default)]
struct OutputLog {
    lines: Vec<String>,
    last_is_transient: bool,
}

impl OutputLog {
    /// Adds a line; `transient` means it ended with '\r'. Returns whether
    /// anything changed on screen.
    fn push(&mut self, text: &str, transient: bool) -> bool {
        let cleaned = clean_output_line(text);
        if cleaned.trim().is_empty() {
            // "\r\n": the newline makes the line before it permanent
            if !transient {
                self.last_is_transient = false;
            }
            return false;
        }
        if self.last_is_transient {
            self.lines.pop();
        }
        self.lines.push(cleaned);
        self.last_is_transient = transient;
        if self.lines.len() > MAX_OUTPUT_LINES {
            self.lines.remove(0);
        }
        true
    }
}

/// Reads a stream to its end, sending each line with a flag telling whether it
/// ended with '\r' (a progress update) rather than '\n'.
fn forward_stream(mut stream: impl Read, tx: &mpsc::Sender<(String, bool)>) {
    let mut pending: Vec<u8> = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let read = match stream.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(read) => read,
        };
        for &byte in &chunk[..read] {
            if byte == b'\n' || byte == b'\r' {
                let text = String::from_utf8_lossy(&pending).into_owned();
                pending.clear();
                if tx.send((text, byte == b'\r')).is_err() {
                    return;
                }
            } else {
                pending.push(byte);
            }
        }
    }
    if !pending.is_empty() {
        let _ = tx.send((String::from_utf8_lossy(&pending).into_owned(), false));
    }
}

/// Strips ANSI escape sequences and control characters from a line of output.
fn clean_output_line(line: &str) -> String {
    let mut cleaned = String::new();
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' {
            // Skip entire ANSI sequence: ESC[ ... final_byte
            if chars.peek() == Some(&'[') {
                chars.next();
                for nc in chars.by_ref() {
                    if nc.is_ascii_alphabetic() || nc == '~' {
                        break;
                    }
                }
            }
        } else if c == '\t' {
            cleaned.push(' ');
        } else if !c.is_control() {
            cleaned.push(c);
        }
    }
    cleaned
}

/// Splits "@home.20260803T0000" or "home.20260803T0000" into subvolume name
/// and timestamp. The timestamp never contains a dot, the subvolume name
/// might. A name counts as a snapshot only when what follows the last dot is
/// a btrbk timestamp: btrbk names snapshots after the subvolume, which may or
/// may not start with "@".
fn split_snapshot_name(name: &str) -> Option<(&str, &str)> {
    let (prefix, timestamp) = name.rsplit_once('.')?;
    (!prefix.is_empty() && parse_btrbk_timestamp(timestamp).is_some()).then_some((prefix, timestamp))
}

/// Groups snapshot names by subvolume: "@" first, then alphabetically, newest
/// snapshot first inside each group. Names that are not snapshots are dropped.
fn group_snapshots(names: impl Iterator<Item = String>) -> SnapshotGroups {
    let mut groups: SnapshotGroups = Vec::new();
    for name in names {
        let Some((prefix, _)) = split_snapshot_name(&name) else {
            continue;
        };
        match groups.iter_mut().find(|(existing, _)| existing == prefix) {
            Some((_, snapshots)) => snapshots.push(name),
            None => groups.push((prefix.to_string(), vec![name])),
        }
    }

    for (_, snapshots) in &mut groups {
        snapshots.sort_by(|a, b| b.cmp(a));
    }
    // "@" is a prefix of every other name, so plain ordering puts it first
    groups.sort_by(|(a, _), (b, _)| a.cmp(b));
    groups
}

/// Parses the timestamp btrbk puts in snapshot names, in any of its
/// timestamp_format flavours (short, long, long-iso), with the optional "_N"
/// suffix btrbk adds when a name is already taken.
fn parse_btrbk_timestamp(timestamp: &str) -> Option<NaiveDateTime> {
    // legacy "YYYYMMDD_HHMMSS" names: here the underscore is not a "_N" suffix
    if let Ok(dt) = NaiveDateTime::parse_from_str(timestamp, "%Y%m%d_%H%M%S") {
        return Some(dt);
    }

    let base = timestamp.split('_').next()?;
    // long-iso carries a UTC offset: the name already is local time, drop it
    let base = base.split(['+', '-']).next()?;

    match base.len() {
        8 => NaiveDate::parse_from_str(base, "%Y%m%d").ok()?.and_hms_opt(0, 0, 0),
        13 => NaiveDateTime::parse_from_str(base, "%Y%m%dT%H%M").ok(),
        15 => NaiveDateTime::parse_from_str(base, "%Y%m%dT%H%M%S").ok(),
        _ => None,
    }
}

/// Decides what a purge deletes. `groups` lists, per subvolume, its snapshots
/// oldest first with their UUID (None when it could not be read).
///
/// The newest snapshot the target also holds is the parent for the next
/// incremental send: it survives, together with everything newer. A subvolume
/// with nothing in common with the target is skipped altogether — its chain is
/// already broken, deleting more would only force a bigger full send.
fn compute_purge_plan(
    groups: &[PurgeCandidates],
    target_uuids: &HashSet<String>,
) -> PurgePlan {
    let mut plan = PurgePlan::default();
    for (prefix, snapshots) in groups {
        if snapshots.len() <= 1 {
            continue;
        }
        let keep_from = snapshots
            .iter()
            .rposition(|(_, uuid)| uuid.as_ref().is_some_and(|uuid| target_uuids.contains(uuid)));
        match keep_from {
            Some(keep_from) => {
                plan.delete
                    .extend(snapshots[..keep_from].iter().map(|(name, _)| name.clone()));
            }
            None => plan.skipped.push(prefix.clone()),
        }
    }
    plan
}

/// Where the configuration may live, most wanted first. Under sudo or pkexec
/// it belongs to the user who invoked the tool, not to root: that is where
/// they create it, since sudo resets HOME to /root. `btrbk_restore` is the
/// name the directory had until 2025-09: it is still read, never written.
fn config_candidates(invoking_home: Option<&Path>, home: Option<&Path>) -> Vec<PathBuf> {
    let mut homes: Vec<&Path> = invoking_home.into_iter().collect();
    if let Some(home) = home
        && !homes.contains(&home)
    {
        homes.push(home);
    }
    // Mai ripiegare su una directory scrivibile da tutti: questo tool gira
    // come root e la config decide su quali path agiscono rename e btrfs delete
    if homes.is_empty() {
        homes.push(Path::new("/root"));
    }
    ["btrbk_tui", "btrbk_restore"]
        .iter()
        .flat_map(|dir| homes.iter().map(move |home| home.join(".config").join(dir).join("config.json")))
        .collect()
}

fn is_legacy_config(path: &Path) -> bool {
    path.parent().and_then(Path::file_name) == Some(OsStr::new("btrbk_restore"))
}

/// The user behind sudo or pkexec.
struct InvokingUser {
    uid: u32,
    gid: u32,
    home: PathBuf,
}

fn invoking_user() -> Option<InvokingUser> {
    let uid: u32 = std::env::var("SUDO_UID")
        .or_else(|_| std::env::var("PKEXEC_UID"))
        .ok()?
        .parse()
        .ok()?;
    if uid == 0 {
        return None;
    }
    // SAFETY: called before any thread is started; the record is copied out
    // before anything else can call into the passwd database
    unsafe {
        let entry = libc::getpwuid(uid);
        if entry.is_null() || (*entry).pw_dir.is_null() {
            return None;
        }
        let home = OsStr::from_bytes(CStr::from_ptr((*entry).pw_dir).to_bytes());
        Some(InvokingUser { uid, gid: (*entry).pw_gid, home: PathBuf::from(home) })
    }
}

/// Subvolume mounted at `mountpoint`, relative to the top level of its
/// filesystem, from the content of /proc/self/mountinfo.
fn mounted_subvolume(mountinfo: &str, mountpoint: &str) -> Option<String> {
    mountinfo
        .lines()
        .filter_map(|line| {
            let (mount, fs) = line.split_once(" - ")?;
            let fields: Vec<&str> = mount.split_whitespace().collect();
            let fstype = fs.split_whitespace().next()?;
            (fstype == "btrfs" && fields.get(4) == Some(&mountpoint)).then(|| fields.get(3).copied())?
        })
        // the last mount on a path is the one in effect
        .next_back()
        .map(|root| root.trim_start_matches('/').to_string())
}

/// First ssh:// target declared in a btrbk configuration, with the ssh options
/// in effect where it is declared.
fn parse_ssh_target(conf: &str) -> Option<SshTarget> {
    let mut user = None;
    let mut identity = None;
    let mut port_option = None;

    for line in conf.lines() {
        let mut parts = line.split_whitespace();
        let (Some(key), Some(value)) = (parts.next(), parts.next()) else {
            continue;
        };
        // "no" and "default" are how btrbk.conf spells "unset"
        let option = (value != "no" && value != "default").then(|| value.to_string());
        match key {
            "ssh_user" => user = option,
            "ssh_identity" => identity = option,
            "ssh_port" => port_option = option,
            "target" => {
                // both "target ssh://..." and "target send-receive ssh://..."
                let url = if value.starts_with("ssh://") { Some(value) } else { parts.next() };
                let Some(rest) = url.and_then(|url| url.strip_prefix("ssh://")) else {
                    continue;
                };
                let (hostport, path) = rest.split_once('/')?;
                // "[::1]:2222" — an IPv6 address has colons of its own
                let (host, port) = match hostport.strip_prefix('[').and_then(|h| h.split_once(']')) {
                    Some((host, after)) => (host, after.strip_prefix(':')),
                    None => match hostport.split_once(':') {
                        Some((host, port)) => (host, Some(port)),
                        None => (hostport, None),
                    },
                };
                if host.is_empty() {
                    return None;
                }
                return Some(SshTarget {
                    host: host.to_string(),
                    port: port.map(str::to_string).or(port_option),
                    path: format!("/{}", path),
                    user,
                    identity,
                });
            }
            _ => {}
        }
    }
    None
}

/// received_uuid values in the output of `btrfs subvolume list -u -R`.
fn parse_received_uuids(output: &str) -> HashSet<String> {
    let mut uuids = HashSet::new();
    for line in output.lines() {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if let Some(index) = fields.iter().position(|f| *f == "received_uuid")
            && let Some(uuid) = fields.get(index + 1)
            && *uuid != "-"
        {
            uuids.insert((*uuid).to_string());
        }
    }
    uuids
}

/// UUID in the output of `btrfs subvolume show`.
fn parse_subvolume_uuid(output: &str) -> Option<String> {
    output.lines().find_map(|line| {
        // plain "UUID:" only — "Parent UUID:" and "Received UUID:" must not match
        line.trim()
            .strip_prefix("UUID:")
            .map(|value| value.trim().to_string())
    })
}

/// received_uuid of every subvolume present on the backup target.
///
/// Fails when the target is not configured or not reachable: the caller must
/// then refuse to purge, since it cannot tell which snapshot is still needed
/// as parent for the next incremental send.
fn target_received_uuids() -> Result<HashSet<String>, String> {
    let conf = fs::read_to_string(BTRBK_CONF)
        .map_err(|err| format!("cannot read {}: {}", BTRBK_CONF, err))?;
    let target = parse_ssh_target(&conf)
        .ok_or_else(|| format!("no ssh target in {}", BTRBK_CONF))?;

    let mut command = Command::new("ssh");
    command.args(["-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]);
    if let Some(port) = &target.port {
        command.args(["-p", port]);
    }
    if let Some(user) = &target.user {
        command.args(["-l", user]);
    }
    if let Some(identity) = &target.identity {
        command.args(["-i", identity]);
    }
    let quoted_path = target.path.replace('\'', r"'\''");
    let output = command
        .arg(&target.host)
        .arg(format!("sudo btrfs subvolume list -u -R '{}'", quoted_path))
        .stdin(Stdio::null())
        .output()
        .map_err(|err| format!("cannot run ssh: {}", err))?;

    if !output.status.success() {
        return Err(format!(
            "backup target unreachable: {}",
            last_line(&output.stderr).unwrap_or_else(|| "ssh failed".to_string())
        ));
    }

    Ok(parse_received_uuids(&String::from_utf8_lossy(&output.stdout)))
}

/// UUID of a local subvolume, or None if it cannot be read.
fn local_subvolume_uuid(path: &Path) -> Option<String> {
    let output = Command::new("btrfs")
        .args(["subvolume", "show"])
        .arg(path)
        .stdin(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_subvolume_uuid(&String::from_utf8_lossy(&output.stdout))
}

/// Last non-empty line of a command's output, cleaned for display.
fn last_line(output: &[u8]) -> Option<String> {
    String::from_utf8_lossy(output)
        .lines()
        .rev()
        .map(clean_output_line)
        .map(|line| line.trim().to_string())
        .find(|line| !line.is_empty())
}

/// Runs a command silently. On failure the error is the last line it wrote to
/// stderr, so the interface can say why.
fn run_command(cmd: &[&str]) -> Result<(), String> {
    let output = Command::new(cmd[0])
        .args(&cmd[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output()
        .map_err(|err| format!("cannot run {}: {}", cmd[0], err))?;

    if output.status.success() {
        Ok(())
    } else {
        Err(last_line(&output.stderr).unwrap_or_else(|| format!("{} failed ({})", cmd[0], output.status)))
    }
}

/// Lowercase ASCII letter for a key code, if it is one.
fn key_char(key: i32) -> Option<char> {
    u8::try_from(key)
        .ok()
        .filter(u8::is_ascii_alphabetic)
        .map(|byte| byte.to_ascii_lowercase() as char)
}

/// Tronca una stringa a `max_chars` caratteri rispettando i confini UTF-8.
/// Evita i panic dello slicing per byte (`&s[..n]`) su caratteri multibyte.
fn truncate_str(s: &str, max_chars: usize) -> String {
    s.chars().take(max_chars).collect()
}

/// Scrive `text` a (y, x) tagliandolo al bordo destro. Coordinate fuori dallo
/// schermo vengono ignorate: nessun disegno può andare in panic o a capo,
/// qualunque sia la dimensione del terminale.
fn put(y: i32, x: i32, text: &str) {
    let (height, width) = get_max_yx();
    if y < 0 || x < 0 || y >= height || x >= width {
        return;
    }
    mvaddstr(y, x, &truncate_str(text, (width - x) as usize));
}

fn put_centered(y: i32, text: &str) {
    let (_, width) = get_max_yx();
    put(y, ((width - text.chars().count() as i32) / 2).max(0), text);
}

fn put_separator(y: i32) {
    let (_, width) = get_max_yx();
    put(y, 0, &"-".repeat(width.max(0) as usize));
}

/// Ridisegna l'area di output mostrando le ultime `height` righe disponibili.
/// Pulisce l'intera area prima di ridisegnare, evitando residui durante lo scroll.
fn render_output_area(lines: &[String], start_y: i32, height: i32) {
    let (_, width) = get_max_yx();
    let blank = " ".repeat(width.max(0) as usize);
    for row in 0..height {
        put(start_y + row, 0, &blank);
    }
    let first = lines.len().saturating_sub(height.max(0) as usize);
    for (idx, line) in lines[first..].iter().enumerate() {
        put(start_y + idx as i32, 0, line);
    }
}

fn get_max_yx() -> (i32, i32) {
    let mut max_y = 0;
    let mut max_x = 0;
    getmaxyx(stdscr(), &mut max_y, &mut max_x);
    (max_y, max_x)
}

/// Prints what a purge would delete, without deleting anything.
fn print_purge_plan(config_file: Option<PathBuf>) -> i32 {
    let app = App::new(config_file);
    match app.plan_purge() {
        Ok(plan) => {
            if plan.delete.is_empty() {
                println!("Nothing to purge.");
            }
            for name in &plan.delete {
                println!("would delete  {}", name);
            }
            for prefix in &plan.skipped {
                println!("skipped       {} (no snapshot in common with the backup target)", prefix);
            }
            0
        }
        Err(reason) => {
            eprintln!("Error: {}", reason);
            1
        }
    }
}

fn main() {
    let mut purge_plan = false;
    let mut config_file = None;
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--purge-plan" => purge_plan = true,
            "--config" | "-c" => match args.next() {
                Some(path) => config_file = Some(PathBuf::from(path)),
                None => {
                    eprintln!("Error: {} needs a file (try --help)", arg);
                    std::process::exit(2);
                }
            },
            "--version" | "-V" => {
                println!("btrbk_tui {}", VERSION);
                return;
            }
            "--help" | "-h" => {
                println!("btrbk_tui {} - restore Btrfs snapshots created with btrbk\n", VERSION);
                println!("Usage: sudo btrbk_tui [OPTION]...\n");
                println!("  -c, --config FILE  use FILE as configuration (default: ~/.config/btrbk_tui/config.json");
                println!("                     of the user who ran sudo)");
                println!("  --purge-plan       show what Purge OLD would delete, then exit");
                println!("  -V, --version      show the version");
                println!("  -h, --help         show this help");
                return;
            }
            other => {
                eprintln!("Error: unknown option '{}' (try --help)", other);
                std::process::exit(2);
            }
        }
    }

    // Check for root privileges
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("Error: This tool requires root privileges.");
        eprintln!("Please run with sudo.");
        std::process::exit(1);
    }

    if purge_plan {
        std::process::exit(print_purge_plan(config_file));
    }

    // Un panic dentro curses lascerebbe il terminale inutilizzabile e il
    // messaggio illeggibile: prima si esce da curses, poi si stampa
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        endwin();
        default_hook(info);
    }));

    // Initialize ncurses
    setlocale(LcCategory::all, "");
    initscr();
    cbreak();
    noecho();
    keypad(stdscr(), true);
    // Senza questo ESC viene riconosciuto solo dopo un secondo
    set_escdelay(25);

    // Create and run the TUI app
    let mut app = App::new(config_file);
    app.run();

    // Cleanup
    endwin();
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONF: &str = "\
transaction_log            /var/log/btrbk.log
ssh_identity /etc/btrbk/ssh/id_ed25519
volume /mnt/btr_pool
  target ssh://10.0.0.1:2222/mnt/backup/host-btrfs
  subvolume @
ssh_user backup
";

    #[test]
    fn ssh_target_is_the_first_one_with_the_options_before_it() {
        assert_eq!(
            parse_ssh_target(CONF),
            Some(SshTarget {
                host: "10.0.0.1".to_string(),
                port: Some("2222".to_string()),
                path: "/mnt/backup/host-btrfs".to_string(),
                // declared after the target: not in effect for it
                user: None,
                identity: Some("/etc/btrbk/ssh/id_ed25519".to_string()),
            })
        );
        assert_eq!(parse_ssh_target("volume /mnt/btr_pool\n  subvolume @\n"), None);
        assert_eq!(parse_ssh_target("target /mnt/local/backup\n"), None);
    }

    #[test]
    fn ssh_target_handles_ports_ipv6_and_target_types() {
        let target = parse_ssh_target("ssh_port 2200\ntarget send-receive ssh://nas.example/backup\n").unwrap();
        assert_eq!((target.host.as_str(), target.port.as_deref()), ("nas.example", Some("2200")));

        let target = parse_ssh_target("ssh_port default\ntarget ssh://[fd00::1]:2222/backup\n").unwrap();
        assert_eq!((target.host.as_str(), target.port.as_deref()), ("fd00::1", Some("2222")));

        let target = parse_ssh_target("ssh_identity no\ntarget ssh://[fd00::1]/backup\n").unwrap();
        assert_eq!((target.port, target.identity), (None, None));
    }

    #[test]
    fn received_uuids_skip_unset_ones() {
        // real `btrfs subvolume list -u -R` output, plus a row with no received_uuid
        let output = "\
ID 33457 gen 27329461 top level 257 parent_uuid 40306c4a-a7f6-b443-a247-da3f59bfc1ca received_uuid c9e62952-9e79-e041-acde-4dc9e3323826 uuid 931a0611-0769-404a-9a79-fddbcc070729 path backup/host-btrfs/@games.20260803T0000
ID 33426 gen 27325696 top level 257 parent_uuid cef19314-dc70-4f4b-931d-8a470cf020b6 received_uuid 17951594-5a9d-5a43-a860-c0de8011da35 uuid 1c8dd660-a0fd-af4c-ad18-6b46f007b6ff path backup/host-btrfs/@games.20260802T1212
ID 257 gen 1 top level 5 parent_uuid - received_uuid - uuid 0ed5ab3d-732e-4544-8522-10abc449a27b path backup
";
        let uuids = parse_received_uuids(output);
        assert_eq!(uuids.len(), 2);
        assert!(uuids.contains("c9e62952-9e79-e041-acde-4dc9e3323826"));
        assert!(uuids.contains("17951594-5a9d-5a43-a860-c0de8011da35"));
    }

    #[test]
    fn subvolume_uuid_ignores_parent_and_received() {
        // "Parent UUID" comes first in real output: a sloppy match would return it
        let output = "\
/mnt/btr_pool/btrbk_snapshots/@games.20260803T0000
\tName: \t\t\t@games.20260803T0000
\tUUID: \t\t\tc9e62952-9e79-e041-acde-4dc9e3323826
\tParent UUID: \t\tf914faf4-aae8-484b-90d4-dae5b2d6088a
\tReceived UUID: \t\t-
";
        assert_eq!(
            parse_subvolume_uuid(output).as_deref(),
            Some("c9e62952-9e79-e041-acde-4dc9e3323826")
        );
        assert_eq!(parse_subvolume_uuid("no uuid here\n"), None);
    }

    #[test]
    fn snapshot_names_split_at_the_last_dot() {
        assert_eq!(split_snapshot_name("@home.20260803T0000"), Some(("@home", "20260803T0000")));
        assert_eq!(split_snapshot_name("@.20260803T0000_1"), Some(("@", "20260803T0000_1")));
        assert_eq!(split_snapshot_name("@my.data.20260803"), Some(("@my.data", "20260803")));
        // btrbk's default naming: the subvolume name, with or without "@"
        assert_eq!(split_snapshot_name("home.20250901T0800"), Some(("home", "20250901T0800")));
        assert_eq!(split_snapshot_name("home"), None);
        assert_eq!(split_snapshot_name("@home."), None);
        assert_eq!(split_snapshot_name(".20250901T0800"), None);
        // a dot alone does not make a snapshot: what follows must be a timestamp
        assert_eq!(split_snapshot_name("scripts.d"), None);
        assert_eq!(split_snapshot_name("prune_snapshots_keep_parent.sh"), None);
        assert_eq!(split_snapshot_name("@home.BROKEN"), None);
    }

    #[test]
    fn config_belongs_to_the_user_behind_sudo() {
        let candidates = config_candidates(Some(Path::new("/home/user")), Some(Path::new("/root")));
        let expected = [
            "/home/user/.config/btrbk_tui/config.json",
            "/root/.config/btrbk_tui/config.json",
            "/home/user/.config/btrbk_restore/config.json",
            "/root/.config/btrbk_restore/config.json",
        ];
        assert_eq!(candidates, expected.iter().map(PathBuf::from).collect::<Vec<_>>());
        assert!(is_legacy_config(&candidates[2]));
        assert!(!is_legacy_config(&candidates[0]));

        // plain root login: no duplicates, and never a world-writable fallback
        assert_eq!(config_candidates(None, Some(Path::new("/root"))).len(), 2);
        assert_eq!(config_candidates(None, None)[0], PathBuf::from("/root/.config/btrbk_tui/config.json"));
    }

    #[test]
    fn mounted_subvolumes_come_from_mountinfo() {
        let mountinfo = "\
23 1 0:21 /@ / rw,relatime shared:1 - btrfs /dev/nvme0n1p2 rw,subvol=/@
24 23 0:21 /@home /home rw,relatime shared:2 - btrfs /dev/nvme0n1p2 rw,subvol=/@home
25 23 0:21 / /mnt/btr_pool rw,relatime shared:3 - btrfs /dev/nvme0n1p2 rw,subvolid=5
26 23 0:22 / /tmp rw shared:4 - tmpfs tmpfs rw
27 24 0:21 /home /home rw,relatime shared:5 - btrfs /dev/sda1 rw,subvol=/home
";
        assert_eq!(mounted_subvolume(mountinfo, "/").as_deref(), Some("@"));
        // mounted twice: the later mount hides the earlier one
        assert_eq!(mounted_subvolume(mountinfo, "/home").as_deref(), Some("home"));
        assert_eq!(mounted_subvolume(mountinfo, "/mnt/btr_pool").as_deref(), Some(""));
        assert_eq!(mounted_subvolume(mountinfo, "/tmp"), None);
        assert_eq!(mounted_subvolume(mountinfo, "/var"), None);
    }

    #[test]
    fn groups_put_root_first_and_newest_on_top() {
        let names = [
            "@home.20260801T0000",
            "@games.20260801T0000",
            "@.20260801T0000",
            "@home.20260803T0000",
            "@home.20260802T0000",
            "not_a_snapshot",
            // a subvolume really called @root is its own group, never "@"
            "@root.20260801T0000",
        ];
        let groups = group_snapshots(names.iter().map(|name| name.to_string()));
        let prefixes: Vec<&str> = groups.iter().map(|(prefix, _)| prefix.as_str()).collect();
        assert_eq!(prefixes, ["@", "@games", "@home", "@root"]);
        assert_eq!(
            groups[2].1,
            ["@home.20260803T0000", "@home.20260802T0000", "@home.20260801T0000"]
        );
    }

    #[test]
    fn timestamps_in_every_btrbk_format() {
        let at = |y, m, d, h, min, s| NaiveDate::from_ymd_opt(y, m, d).unwrap().and_hms_opt(h, min, s);
        assert_eq!(parse_btrbk_timestamp("20260803"), at(2026, 8, 3, 0, 0, 0));
        assert_eq!(parse_btrbk_timestamp("20260803T1405"), at(2026, 8, 3, 14, 5, 0));
        assert_eq!(parse_btrbk_timestamp("20260803T1405_2"), at(2026, 8, 3, 14, 5, 0));
        assert_eq!(parse_btrbk_timestamp("20260803T140559+0200"), at(2026, 8, 3, 14, 5, 59));
        assert_eq!(parse_btrbk_timestamp("20260803T140559-0500_1"), at(2026, 8, 3, 14, 5, 59));
        assert_eq!(parse_btrbk_timestamp("20260803_140559"), at(2026, 8, 3, 14, 5, 59));
        assert_eq!(parse_btrbk_timestamp("BROKEN"), None);
        assert_eq!(parse_btrbk_timestamp("20261399"), None);
    }

    fn group(prefix: &str, snapshots: &[(&str, Option<&str>)]) -> PurgeCandidates {
        (
            prefix.to_string(),
            snapshots
                .iter()
                .map(|(name, uuid)| (name.to_string(), uuid.map(str::to_string)))
                .collect(),
        )
    }

    #[test]
    fn purge_keeps_the_parent_and_everything_newer() {
        let target: HashSet<String> = ["u1", "u2"].iter().map(|u| u.to_string()).collect();
        let groups = [group(
            "@home",
            &[
                ("@home.1", Some("u0")),
                ("@home.2", Some("u1")),
                ("@home.3", Some("u2")), // newest one on the target: the parent
                ("@home.4", Some("u3")),
            ],
        )];
        let plan = compute_purge_plan(&groups, &target);
        assert_eq!(plan.delete, ["@home.1", "@home.2"]);
        assert!(plan.skipped.is_empty());
    }

    #[test]
    fn purge_never_touches_a_broken_chain() {
        let target: HashSet<String> = ["u9".to_string()].into_iter().collect();
        let groups = [
            // nothing in common with the target
            group("@games", &[("@games.1", Some("u1")), ("@games.2", Some("u2"))]),
            // uuid unreadable: treated as not on the target
            group("@home", &[("@home.1", None), ("@home.2", None)]),
            // the parent is the oldest: nothing precedes it
            group("@", &[("@.1", Some("u9")), ("@.2", Some("u4"))]),
            // a single snapshot is never a candidate
            group("@log", &[("@log.1", Some("u5"))]),
        ];
        let plan = compute_purge_plan(&groups, &target);
        assert!(plan.delete.is_empty());
        assert_eq!(plan.skipped, ["@games", "@home"]);

        assert_eq!(compute_purge_plan(&groups, &HashSet::new()).delete, Vec::<String>::new());
    }

    #[test]
    fn output_lines_lose_ansi_and_control_characters() {
        assert_eq!(clean_output_line("\x1b[1;32mdone\x1b[0m"), "done");
        assert_eq!(clean_output_line("a\tb\x07c"), "a bc");
        assert_eq!(clean_output_line("già fatto"), "già fatto");
    }

    #[test]
    fn progress_updates_replace_each_other() {
        let mut log = OutputLog::default();
        log.push("Creating snapshot", false);
        log.push("10MiB 0:00:01", true);
        log.push("20MiB 0:00:02", true);
        assert_eq!(log.lines, ["Creating snapshot", "20MiB 0:00:02"]);

        // "\r\n" ends the meter: its last state stays on screen
        assert!(!log.push("", false));
        log.push("next subvolume", false);
        assert_eq!(log.lines, ["Creating snapshot", "20MiB 0:00:02", "next subvolume"]);
    }

    #[test]
    fn streams_are_split_on_both_line_endings() {
        let (tx, rx) = mpsc::channel();
        forward_stream(&b"one\ntwo\rthree\r\nlast"[..], &tx);
        drop(tx);
        let received: Vec<(String, bool)> = rx.iter().collect();
        let expected = [("one", false), ("two", true), ("three", true), ("", false), ("last", false)];
        assert_eq!(received.len(), expected.len());
        for ((text, transient), (want_text, want_transient)) in received.iter().zip(expected) {
            assert_eq!((text.as_str(), *transient), (want_text, want_transient));
        }
    }

    #[test]
    fn key_char_folds_case_and_ignores_special_keys() {
        assert_eq!(key_char('S' as i32), Some('s'));
        assert_eq!(key_char('q' as i32), Some('q'));
        assert_eq!(key_char(KEY_UP), None);
        assert_eq!(key_char(ERR), None);
        assert_eq!(key_char('1' as i32), None);
    }
}
