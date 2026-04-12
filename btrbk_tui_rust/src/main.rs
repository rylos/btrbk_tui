use ncurses::*;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use chrono::{Local, NaiveDateTime};

#[derive(Serialize, Deserialize, Clone)]
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

struct App {
    config: Config,
    config_path: PathBuf,
    current_screen: String,
    selected_row: i32,
    selected_col: i32,
    status_message: String,
    status_timeout: i32,
    reboot_needed: bool,  // Track if reboot is needed
}

impl App {
    fn new() -> Self {
        let config_path = dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("/tmp"))
            .join(".config")
            .join("btrbk_tui")
            .join("config.json");
        
        let mut app = App {
            config: Config::default(),
            config_path,
            current_screen: "main".to_string(),
            selected_row: 0,
            selected_col: 0,
            status_message: String::new(),
            status_timeout: 0,
            reboot_needed: false,  // Initialize reboot flag
        };
        
        app.load_config();
        app
    }
    
    fn load_config(&mut self) {
        if let Ok(content) = fs::read_to_string(&self.config_path) {
            if let Ok(saved_config) = serde_json::from_str::<Config>(&content) {
                self.config = saved_config;
            }
        }
    }
    
    fn save_config(&self) -> bool {
        if let Some(parent) = self.config_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        
        match serde_json::to_string_pretty(&self.config) {
            Ok(json) => fs::write(&self.config_path, json).is_ok(),
            Err(_) => false,
        }
    }
    
    fn get_snapshots(&self) -> (std::collections::HashMap<String, Vec<String>>, Vec<String>) {
        use std::collections::HashMap;
        
        let mut snapshot_groups: HashMap<String, Vec<String>> = HashMap::new();
        
        match fs::read_dir(&self.config.snapshots_dir) {
            Ok(entries) => {
                for entry in entries {
                    if let Ok(entry) = entry {
                        if entry.path().is_dir() {
                            let name = entry.file_name().to_string_lossy().into_owned();
                            if name.starts_with('@') && name.contains('.') {
                                let prefix = name.split('.').next().unwrap_or("").to_string();
                                snapshot_groups.entry(prefix).or_insert_with(Vec::new).push(name);
                            }
                        }
                    }
                }
            }
            Err(_) => return (HashMap::new(), Vec::new()),
        }
        
        // Sort each group by timestamp (newest first)
        for snapshots in snapshot_groups.values_mut() {
            snapshots.sort_by(|a, b| b.cmp(a));
        }
        
        // Sort prefixes for consistent ordering (@ first, then alphabetically)
        let mut sorted_prefixes: Vec<String> = snapshot_groups.keys().cloned().collect();
        sorted_prefixes.sort_by(|a, b| {
            if a == "@" && b != "@" {
                std::cmp::Ordering::Less
            } else if a != "@" && b == "@" {
                std::cmp::Ordering::Greater
            } else {
                a.cmp(b)
            }
        });
        
        (snapshot_groups, sorted_prefixes)
    }
    
    fn format_snapshot_name(&self, snapshot: &str) -> String {
        if !self.config.show_timestamps {
            return snapshot.to_string();
        }
        
        // Extract timestamp from snapshot name dynamically
        if snapshot.starts_with('@') && snapshot.contains('.') {
            let parts: Vec<&str> = snapshot.split('.').collect();
            if parts.len() >= 2 {
                let timestamp_str = parts[1];
                
                // Try multiple timestamp formats
                if let Ok(dt) = NaiveDateTime::parse_from_str(timestamp_str, "%Y%m%dT%H%M") {
                    return format!("{} ({})", snapshot, dt.format("%Y-%m-%d %H:%M:%S"));
                } else if let Ok(dt) = NaiveDateTime::parse_from_str(timestamp_str, "%Y%m%d_%H%M%S") {
                    return format!("{} ({})", snapshot, dt.format("%Y-%m-%d %H:%M:%S"));
                }
            }
        }
        
        snapshot.to_string()
    }
    
    fn init_colors(&self) {
        start_color();
        use_default_colors();
        
        init_pair(1, COLOR_BLACK, COLOR_CYAN);    // Selected item
        init_pair(2, COLOR_RED, -1);              // Headers
        init_pair(3, COLOR_GREEN, -1);            // Success
        init_pair(4, COLOR_YELLOW, -1);           // Warning
        init_pair(5, COLOR_WHITE, COLOR_BLACK);   // Status bar
        init_pair(6, COLOR_CYAN, -1);             // Info
    }
    
    fn set_status(&mut self, message: &str, timeout: i32) {
        self.status_message = message.to_string();
        self.status_timeout = timeout;
    }
    
    fn create_snapshot(&self) -> (bool, String) {
        use std::process::{Command, Stdio};
        use std::io::{BufRead, BufReader};
        
        let (height, width) = get_max_yx();
        
        // Clear screen and show header
        clear();
        self.draw_header();
        
        // Show operation title
        let title = "Creating Snapshots with btrbk...";
        attron(COLOR_PAIR(2) | A_BOLD());
        mvaddstr(4, (width - title.len() as i32) / 2, title);
        attroff(COLOR_PAIR(2) | A_BOLD());
        
        // Show instructions
        let instruction = "Press ESC to cancel or wait for completion";
        attron(A_DIM());
        mvaddstr(6, (width - instruction.len() as i32) / 2, instruction);
        attroff(A_DIM());
        
        // Simple output area - only horizontal borders
        let output_start_y = 8;
        let output_height = height - 12;
        
        // Draw simple horizontal borders
        let border = "-".repeat(width as usize);
        mvaddstr(output_start_y - 1, 0, &border);
        mvaddstr(output_start_y + output_height, 0, &border);
        
        refresh();
        
        // Set non-blocking input
        timeout(50);
        
        match Command::new("btrbk")
            .args(&["run", "--progress"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())  // Capture stderr too
            .spawn()
        {
            Ok(mut process) => {
                let stdout = process.stdout.take().unwrap();
                let stderr = process.stderr.take().unwrap();
                
                // Use threads to read both stdout and stderr
                use std::sync::mpsc;
                use std::thread;
                
                let (tx, rx) = mpsc::channel();
                let tx_stderr = tx.clone();
                
                // Thread for stdout
                let stdout_thread = thread::spawn(move || {
                    let reader = BufReader::new(stdout);
                    for line in reader.lines() {
                        if let Ok(line_content) = line {
                            let _ = tx.send(line_content);
                        }
                    }
                });
                
                // Thread for stderr
                let stderr_thread = thread::spawn(move || {
                    let reader = BufReader::new(stderr);
                    for line in reader.lines() {
                        if let Ok(line_content) = line {
                            let _ = tx_stderr.send(line_content);
                        }
                    }
                });
                
                let mut output_lines = Vec::new();
                let mut current_line = 0;
                
                // Read from both stdout and stderr
                loop {
                    // Check for ESC key
                    let key = getch();
                    if key == 27 {  // ESC
                        // Safely terminate process and threads
                        let _ = process.kill();
                        
                        // Give threads time to finish reading
                        std::thread::sleep(std::time::Duration::from_millis(100));
                        
                        // Wait for process to actually terminate
                        let _ = process.wait();
                        
                        // Try to join threads with timeout
                        let _ = stdout_thread.join();
                        let _ = stderr_thread.join();
                        
                        timeout(100);
                        return (false, "Operation cancelled by user".to_string());
                    }
                    
                    // Try to receive a line (non-blocking)
                    match rx.try_recv() {
                        Ok(line_content) => {
                            if !line_content.trim().is_empty() {
                                // Clean line: strip ANSI escape sequences and control chars
                                let mut cleaned = String::new();
                                let mut chars = line_content.chars().peekable();
                                while let Some(c) = chars.next() {
                                    if c == '\x1b' {
                                        // Skip entire ANSI sequence: ESC[ ... final_byte
                                        if chars.peek() == Some(&'[') {
                                            chars.next();
                                            while let Some(&nc) = chars.peek() {
                                                chars.next();
                                                if nc.is_ascii_alphabetic() || nc == '~' { break; }
                                            }
                                        }
                                    } else if c == '\r' {
                                        continue;
                                    } else if c.is_ascii_graphic() || c == ' ' {
                                        cleaned.push(c);
                                    }
                                }
                                
                                if cleaned.trim().is_empty() { continue; }
                                
                                // Progress lines: replace last line
                                if cleaned.contains("in @") && cleaned.contains("out @") {
                                    if !output_lines.is_empty() { output_lines.pop(); }
                                }
                                
                                output_lines.push(cleaned.clone());
                                
                                let display_y = output_start_y + output_lines.len() as i32 - 1 - current_line;
                                if display_y >= output_start_y && display_y < output_start_y + output_height {
                                    let display_line = if cleaned.len() > width as usize {
                                        &cleaned[..width as usize]
                                    } else {
                                        &cleaned
                                    };
                                    mvaddstr(display_y, 0, &" ".repeat(width as usize));
                                    mvaddstr(display_y, 0, display_line);
                                }
                                
                                if output_lines.len() > output_height as usize {
                                    current_line = output_lines.len() as i32 - output_height;
                                }
                                
                                refresh();
                            }
                        }
                        Err(mpsc::TryRecvError::Empty) => {
                            // No data available, check if process is still running
                            if let Some(status) = process.try_wait().unwrap_or(None) {
                                // Process finished, drain remaining messages
                                while let Ok(line_content) = rx.try_recv() {
                                    if !line_content.trim().is_empty() {
                                        // Clean line: strip ANSI escape sequences and control chars
                                        let mut cleaned = String::new();
                                        let mut chars = line_content.chars().peekable();
                                        while let Some(c) = chars.next() {
                                            if c == '\x1b' {
                                                if chars.peek() == Some(&'[') {
                                                    chars.next();
                                                    while let Some(&nc) = chars.peek() {
                                                        chars.next();
                                                        if nc.is_ascii_alphabetic() || nc == '~' { break; }
                                                    }
                                                }
                                            } else if c == '\r' {
                                                continue;
                                            } else if c.is_ascii_graphic() || c == ' ' {
                                                cleaned.push(c);
                                            }
                                        }
                                        
                                        if cleaned.trim().is_empty() { continue; }
                                        
                                        if cleaned.contains("in @") && cleaned.contains("out @") {
                                            if !output_lines.is_empty() { output_lines.pop(); }
                                        }
                                        
                                        output_lines.push(cleaned.clone());
                                        
                                        let display_y = output_start_y + output_lines.len() as i32 - 1 - current_line;
                                        if display_y >= output_start_y && display_y < output_start_y + output_height {
                                            let display_line = if cleaned.len() > width as usize {
                                                &cleaned[..width as usize]
                                            } else {
                                                &cleaned
                                            };
                                            mvaddstr(display_y, 0, &" ".repeat(width as usize));
                                            mvaddstr(display_y, 0, display_line);
                                        }
                                        
                                        if output_lines.len() > output_height as usize {
                                            current_line = output_lines.len() as i32 - output_height;
                                        }
                                        
                                        refresh();
                                    }
                                }
                                
                                // Wait for threads to finish
                                let _ = stdout_thread.join();
                                let _ = stderr_thread.join();
                                
                                let return_code = status.success();
                                
                                // Show completion message
                                let completion_msg = if return_code {
                                    "✓ Snapshots created successfully! Press any key to continue..."
                                } else {
                                    "✗ Error creating snapshots! Press any key to continue..."
                                };
                                
                                if return_code {
                                    attron(COLOR_PAIR(3) | A_BOLD());
                                } else {
                                    attron(COLOR_PAIR(4) | A_BOLD());
                                }
                                
                                mvaddstr(height - 2, (width - completion_msg.len() as i32) / 2, completion_msg);
                                
                                if return_code {
                                    attroff(COLOR_PAIR(3) | A_BOLD());
                                } else {
                                    attroff(COLOR_PAIR(4) | A_BOLD());
                                }
                                
                                refresh();
                                
                                // Wait for key press
                                timeout(-1);
                                getch();
                                timeout(100);
                                
                                return (return_code, format!("btrbk completed with status: {}", if return_code { "success" } else { "error" }));
                            }
                            
                            // Small delay to prevent high CPU usage
                            std::thread::sleep(std::time::Duration::from_millis(50));
                        }
                        Err(mpsc::TryRecvError::Disconnected) => {
                            // Channel closed, process finished
                            let return_code = process.wait().map(|status| status.success()).unwrap_or(false);
                            timeout(100);
                            return (return_code, format!("btrbk completed with status: {}", if return_code { "success" } else { "error" }));
                        }
                    }
                }
            }
            Err(_) => {
                timeout(100);  // Restore normal timeout
                (false, "btrbk command not found".to_string())
            }
        }
    }
    
    fn purge_old_snapshots(&self) -> (i32, Vec<String>) {
        let snapshots_dir = &self.config.snapshots_dir;
        
        match fs::read_dir(snapshots_dir) {
            Ok(entries) => {
                let mut all_snapshots: Vec<String> = entries
                    .filter_map(|entry| {
                        let entry = entry.ok()?;
                        if entry.path().is_dir() {
                            let name = entry.file_name().to_string_lossy().into_owned();
                            if name.starts_with('@') && name.contains('.') {
                                Some(entry.path().to_string_lossy().into_owned())
                            } else {
                                None
                            }
                        } else {
                            None
                        }
                    })
                    .collect();
                
                if all_snapshots.is_empty() {
                    return (0, Vec::new());
                }
                
                // Sort snapshots
                all_snapshots.sort();
                
                // Group by type and find old snapshots to delete
                let mut to_delete = Vec::new();
                
                let process_type = |prefix: &str, snapshots: &[String], to_delete: &mut Vec<String>| {
                    let type_snapshots: Vec<&String> = snapshots
                        .iter()
                        .filter(|s| {
                            let basename = s.split('/').last().unwrap_or("");
                            basename.starts_with(&format!("{}.", prefix))
                        })
                        .collect();
                    
                    if type_snapshots.len() > 1 {
                        // Keep the last (most recent) one, delete the rest
                        for snapshot in &type_snapshots[..type_snapshots.len() - 1] {
                            to_delete.push((*snapshot).clone());
                        }
                    }
                };
                
                // Get all unique prefixes dynamically
                let mut prefixes = std::collections::HashSet::new();
                for snapshot_path in &all_snapshots {
                    let basename = snapshot_path.split('/').last().unwrap_or("");
                    if let Some(prefix) = basename.split('.').next() {
                        if prefix.starts_with('@') {
                            prefixes.insert(prefix.to_string());
                        }
                    }
                }
                
                // Process each prefix dynamically
                for prefix in prefixes {
                    process_type(&prefix, &all_snapshots, &mut to_delete);
                }
                
                if to_delete.is_empty() {
                    return (0, Vec::new());
                }
                
                // Delete old snapshots
                let mut deleted_count = 0;
                let deleted_names: Vec<String> = to_delete
                    .iter()
                    .map(|path| path.split('/').last().unwrap_or("").to_string())
                    .collect();
                
                for snapshot_path in &to_delete {
                    if run_command(&["btrfs", "subvolume", "delete", snapshot_path]) {
                        deleted_count += 1;
                    }
                }
                
                (deleted_count, deleted_names)
            }
            Err(_) => (-1, Vec::new()), // Error occurred
        }
    }
    
    fn clean_broken_subvolumes(&self) -> (i32, Vec<String>) {
        let btr_pool_dir = &self.config.btr_pool_dir;
        
        match std::fs::read_dir(btr_pool_dir) {
            Ok(entries) => {
                let mut broken_subvolumes = Vec::new();
                
                // Find all .BROKEN subvolumes
                for entry in entries {
                    if let Ok(entry) = entry {
                        let path = entry.path();
                        if path.is_dir() {
                            if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                                if name.contains(".BROKEN") {
                                    broken_subvolumes.push(path);
                                }
                            }
                        }
                    }
                }
                
                if broken_subvolumes.is_empty() {
                    return (0, Vec::new());
                }
                
                // Delete .BROKEN subvolumes
                let mut deleted_count = 0;
                let mut deleted_names = Vec::new();
                
                for subvol_path in broken_subvolumes {
                    if let Some(name) = subvol_path.file_name().and_then(|n| n.to_str()) {
                        if run_command(&["btrfs", "subvolume", "delete", &subvol_path.to_string_lossy()]) {
                            deleted_count += 1;
                            deleted_names.push(name.to_string());
                        }
                    }
                }
                
                (deleted_count, deleted_names)
            }
            Err(_) => (-1, Vec::new()), // Error occurred
        }
    }
    
    fn draw_header(&self) {
        let (_, width) = get_max_yx();
        
        let title = "BTRBK TUI v2.6";
        attron(COLOR_PAIR(5) | A_BOLD());
        let centered_title = format!("{:^width$}", title, width = width as usize);
        mvaddstr(0, 0, &centered_title[..std::cmp::min(centered_title.len(), width as usize - 1)]);
        attroff(COLOR_PAIR(5) | A_BOLD());
        
        // Separator - no color, full width
        mvaddstr(1, 0, &"-".repeat(width as usize));
    }
    
    fn draw_footer(&self) {
        let (height, width) = get_max_yx();
        
        // Key bindings - show H: Reboot when needed
        let keys = if self.reboot_needed {
            vec![
                "Up/Down: Navigate", "Left/Right: Switch", "ENTER: Select",
                "S: Settings", "R: Refresh", "I: Snapshot", "P: Purge OLD", "B: Clean BROKEN", "H: REBOOT", "Q: Quit"
            ]
        } else {
            vec![
                "Up/Down: Navigate", "Left/Right: Switch", "ENTER: Select",
                "S: Settings", "R: Refresh", "I: Snapshot", "P: Purge OLD", "B: Clean BROKEN", "Q: Quit"
            ]
        };
        let footer_text = keys.join(" | ");
        
        // Separator - no color, full width
        mvaddstr(height - 2, 0, &"-".repeat(width as usize));
        // Footer text with color
        attron(COLOR_PAIR(5));
        mvaddstr(height - 1, 0, &footer_text[..std::cmp::min(footer_text.len(), width as usize - 1)]);
        attroff(COLOR_PAIR(5));
    }
    
    fn draw_status(&mut self) {
        let (height, width) = get_max_yx();
        
        // Show temporary status messages first (if active)
        if !self.status_message.is_empty() && self.status_timeout > 0 {
            attron(COLOR_PAIR(6));
            mvaddstr(height - 3, 0, &self.status_message[..std::cmp::min(self.status_message.len(), width as usize - 1)]);
            attroff(COLOR_PAIR(6));
            self.status_timeout -= 1;
        } else if self.status_timeout <= 0 {
            self.status_message.clear();
            // Show reboot warning only when no temporary messages are active
            if self.reboot_needed {
                attron(COLOR_PAIR(4) | A_BOLD());  // Yellow/Warning color
                let warning_msg = "WARNING: REBOOT REQUIRED - Press H to reboot system";
                mvaddstr(height - 3, 0, &warning_msg[..std::cmp::min(warning_msg.len(), width as usize - 1)]);
                attroff(COLOR_PAIR(4) | A_BOLD());
            }
        } else if self.reboot_needed && self.status_message.is_empty() {
            // Show reboot warning when no temporary messages
            attron(COLOR_PAIR(4) | A_BOLD());
            let warning_msg = "WARNING: REBOOT REQUIRED - Press H to reboot system";
            mvaddstr(height - 3, 0, &warning_msg[..std::cmp::min(warning_msg.len(), width as usize - 1)]);
            attroff(COLOR_PAIR(4) | A_BOLD());
        }
    }
    
    fn draw_main_screen(&self) {
        let (height, width) = get_max_yx();
        let (snapshot_groups, sorted_prefixes) = self.get_snapshots();
        
        if snapshot_groups.is_empty() {
            attron(COLOR_PAIR(4) | A_BOLD());
            mvaddstr(height / 2, (width - 20) / 2, "No snapshots found!");
            attroff(COLOR_PAIR(4) | A_BOLD());
            return;
        }
        
        // Calculate column positions dynamically
        let num_cols = sorted_prefixes.len() as i32;
        let col_width = if num_cols > 0 { (width - 4) / num_cols } else { width - 4 };
        let start_y = 4;
        
        // Draw column headers
        attron(COLOR_PAIR(2) | A_BOLD());
        for (i, prefix) in sorted_prefixes.iter().enumerate() {
            let col_x = 2 + (i as i32) * col_width;
            let snapshots_count = snapshot_groups.get(prefix).map_or(0, |v| v.len());
            let header = format!("{} ({})", prefix.to_uppercase(), snapshots_count);
            mvaddstr(start_y - 1, col_x, &header[..std::cmp::min(header.len(), (col_width - 2) as usize)]);
        }
        attroff(COLOR_PAIR(2) | A_BOLD());
        
        // Draw snapshots for each column
        let max_display = height - 8; // Leave space for header/footer
        let empty_vec = Vec::new();
        
        for (col_idx, prefix) in sorted_prefixes.iter().enumerate() {
            let snapshots = snapshot_groups.get(prefix).unwrap_or(&empty_vec);
            let col_x = 2 + (col_idx as i32) * col_width;
            
            for (i, snapshot) in snapshots.iter().enumerate() {
                if (i as i32) >= max_display || start_y + (i as i32) >= height - 4 {
                    break;
                }
                
                let y = start_y + (i as i32);
                let display_name = self.format_snapshot_name(snapshot);
                
                if self.selected_col == col_idx as i32 && (i as i32) == self.selected_row {
                    attron(COLOR_PAIR(1));
                    mvaddstr(y, col_x, &display_name[..std::cmp::min(display_name.len(), (col_width - 2) as usize)]);
                    attroff(COLOR_PAIR(1));
                } else {
                    mvaddstr(y, col_x, &display_name[..std::cmp::min(display_name.len(), (col_width - 2) as usize)]);
                }
            }
        }
        
        // Show current configuration
        let config_info = format!("Pool: {} | Snapshots: {}", self.config.btr_pool_dir, self.config.snapshots_dir);
        attron(A_DIM());
        mvaddstr(2, 2, &config_info[..std::cmp::min(config_info.len(), (width - 4) as usize)]);
        attroff(A_DIM());
    }
    
    fn draw_settings_screen(&self) {
        let (height, width) = get_max_yx();
        let settings = vec![
            ("BTR Pool Directory", "btr_pool_dir"),
            ("Snapshots Directory", "snapshots_dir"),
            ("Auto Cleanup .BROKEN", "auto_cleanup"),
            ("Confirm Actions", "confirm_actions"),
            ("Show Timestamps", "show_timestamps"),
        ];
        
        let start_y = 4;
        
        attron(COLOR_PAIR(2) | A_BOLD());
        mvaddstr(start_y - 1, 4, "SETTINGS");
        attroff(COLOR_PAIR(2) | A_BOLD());
        
        for (i, (label, key)) in settings.iter().enumerate() {
            if start_y + (i * 2) as i32 >= height - 8 { break; }
            
            let y = start_y + (i * 2) as i32;
            let value = match *key {
                "btr_pool_dir" => &self.config.btr_pool_dir,
                "snapshots_dir" => &self.config.snapshots_dir,
                "auto_cleanup" => if self.config.auto_cleanup { "Yes" } else { "No" },
                "confirm_actions" => if self.config.confirm_actions { "Yes" } else { "No" },
                "show_timestamps" => if self.config.show_timestamps { "Yes" } else { "No" },
                _ => "",
            };
            
            if i as i32 == self.selected_row {
                attron(COLOR_PAIR(1));
            }
            
            mvaddstr(y, 4, &format!("{}:", label)[..std::cmp::min(label.len() + 1, width as usize - 6)]);
            mvaddstr(y + 1, 6, &value[..std::cmp::min(value.len(), width as usize - 8)]);
            
            if i as i32 == self.selected_row {
                attroff(COLOR_PAIR(1));
            }
        }
        
        // Config file info
        attron(A_DIM());
        let config_path = format!("Config: {}", self.config_path.display());
        let config_exists = if self.config_path.exists() { "EXISTS" } else { "NOT FOUND" };
        let config_info = format!("{} ({})", config_path, config_exists);
        mvaddstr(height - 7, 4, &config_info[..std::cmp::min(config_info.len(), width as usize - 6)]);
        mvaddstr(height - 6, 4, "ENTER: Edit | SPACE: Toggle | ESC: Back | S: Save");
        attroff(A_DIM());
    }
    
    fn confirm_dialog(&self, message: &str) -> bool {
        if !self.config.confirm_actions {
            return true;
        }
        
        let (height, width) = get_max_yx();
        let dialog_width = std::cmp::min(message.len() + 10, width as usize - 4);
        let dialog_height = 5;
        let dialog_y = height / 2 - 2;
        let dialog_x = (width as usize - dialog_width) / 2;
        
        // Draw dialog
        for i in 0..dialog_height {
            mvaddstr(dialog_y + i, dialog_x as i32, &" ".repeat(dialog_width));
        }
        
        let top_border = format!("+{}+", "-".repeat(dialog_width - 2));
        mvaddstr(dialog_y, dialog_x as i32, &top_border);
        mvaddstr(dialog_y + dialog_height - 1, dialog_x as i32, &top_border);
        for i in 1..dialog_height - 1 {
            mvaddstr(dialog_y + i, dialog_x as i32, "|");
            mvaddstr(dialog_y + i, (dialog_x + dialog_width - 1) as i32, "|");
        }
        
        mvaddstr(dialog_y + 1, (dialog_x + 2) as i32, &message[..std::cmp::min(message.len(), dialog_width - 4)]);
        mvaddstr(dialog_y + 3, (dialog_x + 2) as i32, "Y: Yes | N: No");
        refresh();
        
        loop {
            match getch() {
                121 | 89 => return true,  // 'y' or 'Y'
                110 | 78 | 27 => return false,  // 'n' or 'N' or ESC
                _ => continue,
            }
        }
    }
    
    fn restore_snapshot(&self, snapshot: &str, snapshot_type: &str) -> bool {
        let source_path = Path::new(&self.config.snapshots_dir).join(snapshot);
        
        // Dynamic subvolume path generation
        let subvol_name = if snapshot_type.is_empty() || snapshot_type == "root" {
            "@".to_string()
        } else {
            format!("@{}", snapshot_type)
        };
        
        let current_subvol = Path::new(&self.config.btr_pool_dir).join(&subvol_name);
        // Generate unique .BROKEN name with timestamp
        let timestamp = Local::now().format("%Y%m%d_%H%M%S");
        let broken_subvol = Path::new(&self.config.btr_pool_dir).join(format!("{}.BROKEN.{}", subvol_name, timestamp));
        let new_subvol = Path::new(&self.config.btr_pool_dir).join(&subvol_name);
        
        // Move current to .BROKEN
        if !run_command(&["mv", &current_subvol.to_string_lossy(), &broken_subvol.to_string_lossy()]) {
            return false;
        }
        
        // Create new snapshot
        if !run_command(&["btrfs", "subvolume", "snapshot", &source_path.to_string_lossy(), &new_subvol.to_string_lossy()]) {
            // Rollback: ripristina il subvolume originale
            run_command(&["mv", &broken_subvol.to_string_lossy(), &current_subvol.to_string_lossy()]);
            return false;
        }
        
        // Verifica che il restore sia andato a buon fine
        let restore_successful = self.verify_restore_success(&new_subvol, snapshot_type);
        
        if !restore_successful {
            // Rollback completo: rimuovi il subvolume fallito e ripristina l'originale
            run_command(&["btrfs", "subvolume", "delete", &new_subvol.to_string_lossy()]);
            run_command(&["mv", &broken_subvol.to_string_lossy(), &current_subvol.to_string_lossy()]);
            return false;
        }
        
        // Auto cleanup if enabled - rimuovi .BROKEN solo se il restore è andato a buon fine
        if self.config.auto_cleanup {
            run_command(&["btrfs", "subvolume", "delete", &broken_subvol.to_string_lossy()]);
        }
        
        true
    }
    
    fn verify_restore_success(&self, restored_subvol: &Path, snapshot_type: &str) -> bool {
        // 1. Verifica che il subvolume esista
        if !restored_subvol.exists() {
            return false;
        }
        
        // 2. Verifica che sia un subvolume btrfs valido
        if !run_command(&["btrfs", "subvolume", "show", &restored_subvol.to_string_lossy()]) {
            return false;
        }
        
        // 3. Verifica file/directory critici in base al tipo di subvolume
        match snapshot_type {
            "root" => {
                let critical_dirs = ["etc", "usr", "var", "bin"];
                for dir in &critical_dirs {
                    if !restored_subvol.join(dir).exists() {
                        return false;
                    }
                }
                let critical_files = ["etc/fstab", "etc/passwd"];
                for file in &critical_files {
                    let file_path = restored_subvol.join(file);
                    if !file_path.exists() || !file_path.is_file() {
                        return false;
                    }
                }
            }
            "home" => {
                match fs::read_dir(restored_subvol) {
                    Ok(entries) => {
                        if entries.count() == 0 {
                            return false;
                        }
                    }
                    Err(_) => return false,
                }
            }
            _ => {
                // Per qualsiasi altro tipo (@games, @work, @custom, ecc.):
                // verifica solo che sia leggibile
                if fs::read_dir(restored_subvol).is_err() {
                    return false;
                }
            }
        }
        
        true
    }
    
    fn handle_main_input(&mut self, key: i32) {
        let (snapshot_groups, sorted_prefixes) = self.get_snapshots();
        
        if sorted_prefixes.is_empty() {
            return;
        }
        
        // Ensure selected_col is within bounds
        if self.selected_col >= sorted_prefixes.len() as i32 {
            self.selected_col = (sorted_prefixes.len() as i32) - 1;
        }
        
        let empty_vec = Vec::new();
        let current_snapshots = snapshot_groups.get(&sorted_prefixes[self.selected_col as usize]).unwrap_or(&empty_vec);
        
        match key {
            KEY_UP => {
                if self.selected_row > 0 {
                    self.selected_row -= 1;
                }
            }
            KEY_DOWN => {
                if self.selected_row < (current_snapshots.len() as i32) - 1 {
                    self.selected_row += 1;
                }
            }
            KEY_LEFT => {
                if self.selected_col > 0 {
                    self.selected_col -= 1;
                    // Adjust row if new column has fewer items
                    let empty_vec = Vec::new();
                    let new_snapshots = snapshot_groups.get(&sorted_prefixes[self.selected_col as usize]).unwrap_or(&empty_vec);
                    if self.selected_row >= new_snapshots.len() as i32 && !new_snapshots.is_empty() {
                        self.selected_row = (new_snapshots.len() as i32) - 1;
                    } else if new_snapshots.is_empty() {
                        self.selected_row = 0;
                    }
                }
            }
            KEY_RIGHT => {
                if self.selected_col < (sorted_prefixes.len() as i32) - 1 {
                    self.selected_col += 1;
                    // Adjust row if new column has fewer items
                    let empty_vec = Vec::new();
                    let new_snapshots = snapshot_groups.get(&sorted_prefixes[self.selected_col as usize]).unwrap_or(&empty_vec);
                    if self.selected_row >= new_snapshots.len() as i32 && !new_snapshots.is_empty() {
                        self.selected_row = (new_snapshots.len() as i32) - 1;
                    } else if new_snapshots.is_empty() {
                        self.selected_row = 0;
                    }
                }
            }
            10 | 13 => {  // Enter
                self.handle_snapshot_selection(&snapshot_groups, &sorted_prefixes);
            }
            115 | 83 => {  // 's' or 'S'
                self.current_screen = "settings".to_string();
                self.selected_row = 0;
            }
            114 | 82 => {  // 'r' or 'R'
                self.set_status("Snapshots refreshed", 30);
            }
            104 | 72 => {  // 'h' or 'H'
                if self.reboot_needed {
                    if self.confirm_dialog("Reboot system now?") {
                        run_command(&["reboot"]);
                    } else {
                        self.set_status("Reboot cancelled", 30);
                    }
                }
            }
            112 | 80 => {  // 'p' or 'P'
                if self.confirm_dialog("Purge old snapshots (keep only most recent)?") {
                    self.set_status("Purging old snapshots...", 30);
                    refresh();
                    
                    let (deleted_count, _deleted_list) = self.purge_old_snapshots();
                    
                    if deleted_count == -1 {
                        self.set_status("Error: cannot read snapshots directory", 100);
                    } else if deleted_count == 0 {
                        self.set_status("No old snapshots to purge", 50);
                    } else {
                        self.set_status(&format!("Purged {} old snapshots successfully", deleted_count), 150);
                    }
                } else {
                    self.set_status("Purge cancelled", 30);
                }
            }
            98 | 66 => {  // 'b' or 'B'
                if self.confirm_dialog("Delete all .BROKEN subvolumes?") {
                    self.set_status("Cleaning .BROKEN subvolumes...", 30);
                    refresh();
                    
                    let (deleted_count, _deleted_list) = self.clean_broken_subvolumes();
                    
                    if deleted_count == -1 {
                        self.set_status("Error: cannot read pool directory", 100);
                    } else if deleted_count == 0 {
                        self.set_status("No .BROKEN subvolumes found", 50);
                    } else {
                        self.set_status(&format!("Cleaned {} .BROKEN subvolumes successfully", deleted_count), 150);
                    }
                } else {
                    self.set_status("Clean cancelled", 30);
                }
            }
            105 | 73 => {  // 'i' or 'I'
                if self.confirm_dialog("Create new snapshots with btrbk?") {
                    let (success, message) = self.create_snapshot();
                    if success {
                        self.set_status("Snapshots created successfully", 100);
                    } else {
                        self.set_status(&format!("Snapshot creation failed: {}", message), 150);
                    }
                } else {
                    self.set_status("Snapshot creation cancelled", 30);
                }
            }
            _ => {}
        }
    }
    
    fn handle_snapshot_selection(&mut self, snapshot_groups: &std::collections::HashMap<String, Vec<String>>, sorted_prefixes: &[String]) {
        if sorted_prefixes.is_empty() || self.selected_col >= sorted_prefixes.len() as i32 {
            return;
        }
        
        let current_prefix = &sorted_prefixes[self.selected_col as usize];
        let empty_vec = Vec::new();
        let current_snapshots = snapshot_groups.get(current_prefix).unwrap_or(&empty_vec);
        
        if current_snapshots.is_empty() || self.selected_row >= current_snapshots.len() as i32 {
            return;
        }
        
        let snapshot = &current_snapshots[self.selected_row as usize];
        
        // Extract snapshot type from prefix
        let snapshot_type = if current_prefix == "@" {
            "root"  // Special case for root subvolume
        } else if current_prefix.starts_with('@') {
            &current_prefix[1..]  // Remove @ prefix for others
        } else {
            current_prefix
        };
        
        if !self.confirm_dialog(&format!("Restore {} snapshot?", snapshot_type)) {
            self.set_status("Restore cancelled", 30);
            return;
        }
        
        self.set_status("Restoring snapshot...", 30);
        refresh();
        
        if self.restore_snapshot(snapshot, snapshot_type) {
            self.reboot_needed = true;
            self.set_status(&format!("{} snapshot restored! Press H to reboot when ready", snapshot_type), 150);
        } else {
            self.set_status(&format!("Error: {} snapshot restore failed (rolled back)", snapshot_type), 150);
        }
    }
    
    fn handle_settings_input(&mut self, key: i32) {
        match key {
            KEY_UP => {
                if self.selected_row > 0 {
                    self.selected_row -= 1;
                }
            }
            KEY_DOWN => {
                if self.selected_row < 4 {
                    self.selected_row += 1;
                }
            }
            10 | 13 => {  // ENTER
                self.edit_setting();
            }
            32 => {  // SPACE
                self.toggle_setting();
            }
            115 | 83 => {  // 's' or 'S'
                if self.save_config() {
                    self.set_status("Configuration saved", 50);
                } else {
                    self.set_status("Error: failed to save configuration", 100);
                }
            }
            27 => {  // ESC
                self.current_screen = "main".to_string();
                self.selected_row = 0;
            }
            _ => {}
        }
    }
    
    fn edit_setting(&mut self) {
        match self.selected_row {
            0 | 1 => {  // String settings
                let (height, width) = get_max_yx();
                let field_name = if self.selected_row == 0 { "btr_pool_dir" } else { "snapshots_dir" };
                let current_value = if self.selected_row == 0 { &self.config.btr_pool_dir } else { &self.config.snapshots_dir };
                
                // Clear area for input
                for i in 0..5 {
                    mvaddstr(height / 2 - 2 + i, 4, &" ".repeat(width as usize - 8));
                }
                
                mvaddstr(height / 2 - 1, 4, &format!("Edit {}: ", field_name));
                mvaddstr(height / 2, 4, &format!("Current: {}", current_value));
                mvaddstr(height / 2 + 1, 4, "New: ");
                mvaddstr(height / 2 + 3, 4, "Press ENTER to confirm, ESC to cancel");
                refresh();
                
                curs_set(CURSOR_VISIBILITY::CURSOR_VISIBLE);
                
                let mut input = String::new();
                let mut ch = getch();
                
                while ch != 10 && ch != 13 && ch != 27 {
                    if ch == KEY_BACKSPACE || ch == 127 || ch == 8 {
                        if !input.is_empty() {
                            input.pop();
                            mvaddstr(height / 2 + 1, 9, &format!("{} ", input));
                        }
                    } else if ch >= 32 && ch < 127 {
                        input.push(ch as u8 as char);
                        mvaddstr(height / 2 + 1, 9, &input);
                    }
                    refresh();
                    ch = getch();
                }
                
                curs_set(CURSOR_VISIBILITY::CURSOR_INVISIBLE);
                
                if ch != 27 && !input.trim().is_empty() {
                    if self.selected_row == 0 {
                        self.config.btr_pool_dir = input.trim().to_string();
                    } else {
                        self.config.snapshots_dir = input.trim().to_string();
                    }
                    self.save_config();
                    self.set_status(&format!("Updated {}", field_name), 50);
                } else {
                    self.set_status("Edit cancelled", 30);
                }
            }
            2 | 3 | 4 => {  // Boolean settings
                self.toggle_setting();
            }
            _ => {}
        }
    }
    
    fn toggle_setting(&mut self) {
        let (name, toggled) = match self.selected_row {
            2 => { self.config.auto_cleanup = !self.config.auto_cleanup; ("Auto cleanup", self.config.auto_cleanup) }
            3 => { self.config.confirm_actions = !self.config.confirm_actions; ("Confirm actions", self.config.confirm_actions) }
            4 => { self.config.show_timestamps = !self.config.show_timestamps; ("Show timestamps", self.config.show_timestamps) }
            _ => return,
        };
        self.save_config();
        self.set_status(&format!("{}: {}", name, if toggled { "Yes" } else { "No" }), 50);
    }
    
    fn run(&mut self) {
        curs_set(CURSOR_VISIBILITY::CURSOR_INVISIBLE);
        timeout(100);
        self.init_colors();
        
        loop {
            clear();
            
            self.draw_header();
            
            match self.current_screen.as_str() {
                "main" => self.draw_main_screen(),
                "settings" => self.draw_settings_screen(),
                _ => {}
            }
            
            self.draw_status();
            self.draw_footer();
            
            refresh();
            
            let key = getch();
            
            if key == -1 {
                continue;
            } else if key == 113 || key == 81 {  // 'q' or 'Q'
                break;
            } else {
                match self.current_screen.as_str() {
                    "main" => self.handle_main_input(key),
                    "settings" => self.handle_settings_input(key),
                    _ => {}
                }
            }
        }
    }
}

fn run_command(cmd: &[&str]) -> bool {
    Command::new(cmd[0])
        .args(&cmd[1..])
        .stdout(std::process::Stdio::null())  // Hide stdout
        .stderr(std::process::Stdio::null())  // Hide stderr
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn get_max_yx() -> (i32, i32) {
    let mut max_y = 0;
    let mut max_x = 0;
    getmaxyx(stdscr(), &mut max_y, &mut max_x);
    (max_y, max_x)
}

fn main() {
    // Check for root privileges
    if unsafe { libc::geteuid() } != 0 {
        eprintln!("Error: This tool requires root privileges.");
        eprintln!("Please run with sudo.");
        std::process::exit(1);
    }
    
    // Initialize ncurses
    initscr();
    cbreak();
    noecho();
    keypad(stdscr(), true);
    
    // Create and run the TUI app
    let mut app = App::new();
    app.run();
    
    // Cleanup
    endwin();
}
