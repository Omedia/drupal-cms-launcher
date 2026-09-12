use anyhow::{Context, Result, ensure};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{BufRead, Read, Write},
    net::{TcpListener, TcpStream},
    os::unix::{fs::OpenOptionsExt, fs::PermissionsExt, process::CommandExt},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

/// Opt-in local diagnostics for the benchmark harness; no output by default.
pub fn record_timing(stage: &str, url: Option<&str>) {
    let Some(path) = std::env::var_os("DRUPAL_CMS_TIMING_FILE") else {
        return;
    };
    let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return;
    };
    let entry = serde_json::json!({
        "stage": stage, "unix_ns": now.as_nanos(), "pid": std::process::id(), "url": url,
    });
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(path)
    {
        let _ = writeln!(file, "{entry}");
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Event {
    pub phase: String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

fn emit(phase: &str, message: &str, url: Option<String>) {
    record_timing(&format!("runtime.{phase}"), url.as_deref());
    let event = Event {
        phase: phase.into(),
        message: message.into(),
        url,
    };
    if let Ok(json) = serde_json::to_string(&event) {
        let _ = writeln!(std::io::stdout().lock(), "{json}");
    }
}

#[derive(Deserialize, Serialize)]
struct State {
    format: u32,
    hash_salt: String,
    /// Where the data directory was on the previous run. Absent before this
    /// field existed, which is treated the same as having moved.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    data_path: Option<String>,
}

#[derive(Clone)]
pub struct Paths {
    pub resources: PathBuf,
    pub data: PathBuf,
}

impl Paths {
    pub fn discover() -> Result<Self> {
        let executable = std::env::current_exe()?;
        let bundled = executable
            .parent()
            .context("Missing executable directory")?
            .join("../Resources");
        let resources = if bundled.join("manifest.json").exists() {
            bundled
        } else {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("build/resources")
        };
        let home = std::env::var_os("HOME").context("Cannot find your home directory")?;
        let support = PathBuf::from(home).join("Library/Application Support");
        let data = support.join("Drupal CMS Launcher");
        // The app briefly shipped under a different name. Move a site created
        // then rather than stranding it, once, and only when there is nothing
        // to overwrite. Drupal's cached paths are repaired separately, by
        // clear_caches_if_moved.
        let previous = support.join("CMS Runcher");
        if !data.exists() && previous.is_dir() {
            fs::rename(&previous, &data).with_context(|| {
                format!(
                    "Could not move your existing site from {} to {}. It has not been changed.",
                    previous.display(),
                    data.display()
                )
            })?;
        }
        Ok(Self { resources, data })
    }

    pub fn validate(&self) -> Result<()> {
        for file in [
            "php/bin/php",
            "site.asif",
            "manifest.json",
            "router.php",
            "settings.php",
        ] {
            ensure!(
                self.resources.join(file).is_file(),
                "The app is missing {file}. Build or reinstall the complete app bundle."
            );
        }
        Ok(())
    }

    fn php(&self) -> Command {
        let mut command = Command::new(self.resources.join("php/bin/php"));
        command
            .args([
                "-n",
                "-d",
                "memory_limit=512M",
                "-d",
                "display_errors=stderr",
                "-d",
                "log_errors=1",
                "-d",
                "opcache.enable_cli=1",
                "-d",
                "opcache.memory_consumption=256",
                "-d",
                "opcache.max_accelerated_files=20000",
                "-d",
                "opcache.enable_file_override=1",
            ])
            .env_remove("PHPRC")
            .env("PHP_INI_SCAN_DIR", "")
            .env(
                "PATH",
                format!("{}:/usr/bin:/bin", self.resources.join("php/bin").display()),
            )
            .env("DRUPAL_CMS_DATA", &self.data)
            .env("DRUPAL_CMS_RESOURCES", &self.resources)
            // Sites built before these variables were renamed have a
            // settings.php stub that reads the old name. The stub lives inside
            // the site image, which is never rewritten once it exists, so the
            // old name has to keep working for those sites to open at all.
            .env("DRUPINST_RESOURCES", &self.resources);
        let apcu = self.resources.join("php/lib/apcu.so");
        if apcu.is_file() {
            command
                .arg("-d")
                .arg(format!("extension={}", apcu.display()))
                .args(["-d", "apc.enable_cli=1"]);
        }
        command
    }
}

fn secret() -> Result<String> {
    let mut bytes = [0_u8; 32];
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn private_directory(path: &Path) -> Result<()> {
    fs::create_dir_all(path)?;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

fn write_state(path: &Path, state: &State) -> Result<()> {
    let temporary = path.with_extension("json.new");
    let mut file = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .mode(0o600)
        .open(&temporary)?;
    serde_json::to_writer_pretty(&mut file, state)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn load_state(paths: &Paths) -> Result<State> {
    let path = paths.data.join("state.json");
    if path.exists() {
        let state: State = serde_json::from_reader(File::open(path)?)
            .context("Site settings could not be read. Existing data has been preserved.")?;
        ensure!(
            state.format == 2,
            "This site was created by an earlier app version with a MySQL database. Existing data has been preserved; use that version to open it."
        );
        return Ok(state);
    }
    ensure!(
        !paths.data.join("database").exists() && !paths.data.join("mysql").exists(),
        "A database exists without its settings. Existing data has been preserved; restore state.json before continuing."
    );
    let state = State {
        format: 2,
        hash_salt: secret()?,
        data_path: Some(paths.data.to_string_lossy().into_owned()),
    };
    write_state(&path, &state)?;
    Ok(state)
}

/// Drupal stores absolute paths in its caches, including the compiled service
/// container. If the data directory has moved since the last run, because the
/// app was renamed, a backup was restored elsewhere, or the account changed,
/// those paths no longer resolve and the site cannot boot. Emptying the cache
/// tables makes Drupal rebuild them against the current location; caches are
/// rebuildable by definition, so nothing is lost.
fn clear_caches_if_moved(paths: &Paths, state: &mut State, stop: &AtomicBool) -> Result<()> {
    let current = paths.data.to_string_lossy().into_owned();
    if state.data_path.as_deref() == Some(current.as_str()) {
        return Ok(());
    }
    if paths.data.join("database/drupal.sqlite").is_file() {
        emit("preparing", "Updating your site's location…", None);
        let mut clear = paths.php();
        clear.arg("-r").arg(
            r#"$db = new PDO('sqlite:' . getenv('DRUPAL_CMS_DATA') . '/database/drupal.sqlite', NULL, NULL, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
               $tables = $db->query("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'cache_%'")->fetchAll(PDO::FETCH_COLUMN);
               foreach ($tables as $table) { $db->exec('DELETE FROM "' . $table . '"'); }"#,
        );
        run_checked(
            clear,
            &paths.data.join("logs/setup.log"),
            stop,
            Duration::from_secs(120),
        )?;
    }
    state.data_path = Some(current);
    write_state(&paths.data.join("state.json"), state)?;
    Ok(())
}

struct Process {
    child: Child,
    stopped: bool,
}

impl Process {
    fn start(mut command: Command, log: &Path) -> Result<Self> {
        let output = OpenOptions::new()
            .create(true)
            .append(true)
            .mode(0o600)
            .open(log)?;
        command
            .stdin(Stdio::null())
            .stdout(output.try_clone()?)
            .stderr(output)
            .process_group(0);
        Ok(Self {
            child: command
                .spawn()
                .context("Could not start the bundled runtime")?,
            stopped: false,
        })
    }

    fn running(&mut self) -> Result<bool> {
        Ok(self.child.try_wait()?.is_none())
    }

    fn terminate(&mut self) {
        if self.stopped {
            return;
        }
        self.stopped = true;
        // Each child starts its own group; this also stops PHP's worker processes.
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGTERM);
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        unsafe {
            libc::kill(-(self.child.id() as i32), libc::SIGKILL);
        }
        let _ = self.child.wait();
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        self.terminate();
    }
}

fn run_checked(command: Command, log: &Path, stop: &AtomicBool, timeout: Duration) -> Result<()> {
    let mut process = Process::start(command, log)?;
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = process.child.try_wait()? {
            ensure!(
                status.success(),
                "Setup did not complete. See {} for details.",
                log.display()
            );
            return Ok(());
        }
        ensure!(!stop.load(Ordering::Relaxed), "Startup cancelled");
        ensure!(
            Instant::now() < deadline,
            "Setup timed out. See {} for details.",
            log.display()
        );
        thread::sleep(Duration::from_millis(100));
    }
}

/// Clones the bundled disk image into the data directory and attaches it at
/// `site/`. Cloning is an APFS copy-on-write clone, so it is near-instant.
fn attach_site(paths: &Paths, stop: &AtomicBool) -> Result<()> {
    let site = paths.data.join("site");
    if site.join("web/index.php").is_file() {
        // Already attached, or a site directory from an earlier app version.
        return Ok(());
    }
    record_timing("site.attach.begin", None);
    let setup_log = paths.data.join("logs/setup.log");
    let image = paths.data.join("site.asif");
    if !image.is_file() {
        let staging = paths.data.join("site.asif.new");
        let mut clone = Command::new("/bin/cp");
        clone
            .arg("-c")
            .arg(paths.resources.join("site.asif"))
            .arg(&staging);
        run_checked(clone, &setup_log, stop, Duration::from_secs(180))?;
        fs::set_permissions(&staging, fs::Permissions::from_mode(0o600))?;
        fs::rename(&staging, &image)?;
    }
    private_directory(&site)?;
    let mut attach = Command::new("/usr/sbin/diskutil");
    attach
        .args(["image", "attach", "--nobrowse", "--mountPoint"])
        .arg(&site)
        .arg(&image);
    run_checked(attach, &setup_log, stop, Duration::from_secs(60))?;
    ensure!(
        site.join("web/index.php").is_file() && site.join("vendor/autoload.php").is_file(),
        "The bundled site image is incomplete"
    );
    record_timing("site.attach.end", None);
    Ok(())
}

/// Detaches the site image when the supervisor exits, however it exits.
struct SiteVolume(Option<PathBuf>);
impl Drop for SiteVolume {
    fn drop(&mut self) {
        if let Some(site) = &self.0 {
            let _ = Command::new("/usr/sbin/diskutil")
                .arg("eject")
                .arg(site)
                .output();
        }
    }
}

fn http_ready(port: u16) -> bool {
    let Ok(mut stream) =
        TcpStream::connect_timeout(&([127, 0, 0, 1], port).into(), Duration::from_millis(250))
    else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    if write!(
        stream,
        "GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    )
    .is_err()
    {
        return false;
    }
    let mut bytes = [0; 128];
    let Ok(count) = stream.read(&mut bytes) else {
        return false;
    };
    let response = String::from_utf8_lossy(&bytes[..count]);
    response.starts_with("HTTP/1.1 200")
        || response.starts_with("HTTP/1.1 30")
        || response.starts_with("HTTP/1.1 404")
}

pub fn supervise(paths: Paths) -> Result<()> {
    record_timing("supervisor.begin", None);
    paths.validate()?;
    private_directory(&paths.data)?;
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .mode(0o600)
        .open(paths.data.join("launcher.lock"))?;
    lock.try_lock()
        .context("This site is already running in another Drupal CMS Launcher instance")?;
    for directory in ["logs", "private", "tmp"] {
        private_directory(&paths.data.join(directory))?;
    }
    let mut state = load_state(&paths)?;
    let (sender, receiver) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let reader_stop = stop.clone();
    thread::spawn(move || {
        for line in std::io::stdin().lock().lines() {
            let Ok(line) = line else {
                break;
            };
            if line == "STOP" {
                break;
            }
            if line == "OPEN" {
                let _ = sender.send(());
            }
        }
        reader_stop.store(true, Ordering::Relaxed);
    });
    emit("preparing", "Preparing your site…", None);
    clear_caches_if_moved(&paths, &mut state, &stop)?;
    attach_site(&paths, &stop)?;
    // Declared before the server so it is dropped after it.
    let site_volume = SiteVolume(
        paths
            .data
            .join("site.asif")
            .is_file()
            .then(|| paths.data.join("site")),
    );
    // SQLite runs inside PHP; Drupal creates the file during installation.
    private_directory(&paths.data.join("database"))?;
    let port = TcpListener::bind(("127.0.0.1", 0))?.local_addr()?.port();
    let url = format!("http://127.0.0.1:{port}");
    emit("starting", "Opening Drupal…", None);
    let mut php = paths.php();
    php.env("PHP_CLI_SERVER_WORKERS", "4")
        .args([
            "-d",
            "max_execution_time=120",
            "-d",
            "upload_max_filesize=32M",
            "-d",
            "post_max_size=32M",
            "-S",
            &format!("127.0.0.1:{port}"),
            "-t",
        ])
        .arg(paths.data.join("site/web"))
        .arg(paths.resources.join("router.php"))
        .current_dir(paths.data.join("site/web"));
    record_timing("php.start", None);
    let mut server = Process::start(php, &paths.data.join("logs/php.log"))?;
    let deadline = Instant::now() + Duration::from_secs(60);
    while !http_ready(port) {
        ensure!(
            server.running()?,
            "The site stopped. Open the PHP log for details."
        );
        ensure!(!stop.load(Ordering::Relaxed), "Startup cancelled");
        ensure!(
            Instant::now() < deadline,
            "Drupal did not become ready. Open the PHP log for details."
        );
        thread::sleep(Duration::from_millis(200));
    }
    emit("ready", "Your site is running", Some(url.clone()));
    while !stop.load(Ordering::Relaxed) {
        ensure!(
            server.running()?,
            "The site stopped. Your saved data is still on disk."
        );
        if receiver.recv_timeout(Duration::from_millis(200)).is_ok() {
            emit("open", "Opening Drupal CMS", Some(url.clone()));
        }
    }
    emit("stopping", "Saving and stopping your site…", None);
    server.terminate();
    drop(site_volume);
    emit("stopped", "Your site is stopped", None);
    Ok(())
}

pub fn supervisor_entry(paths: Paths) -> i32 {
    match supervise(paths) {
        Ok(()) => 0,
        Err(error) => {
            emit("error", &format!("{error:#}"), None);
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn existing_database_is_never_reinitialized_without_its_state() {
        let root =
            std::env::temp_dir().join(format!("drupal-cms-launcher-test-{}", secret().unwrap()));
        private_directory(&root).unwrap();
        let paths = Paths {
            resources: root.clone(),
            data: root.clone(),
        };
        fs::create_dir(root.join("database")).unwrap();
        fs::write(root.join("database/drupal.sqlite"), "preserve me").unwrap();
        assert!(load_state(&paths).is_err());
        assert_eq!(
            fs::read_to_string(root.join("database/drupal.sqlite")).unwrap(),
            "preserve me"
        );
        fs::remove_dir_all(root.join("database")).unwrap();
        let first = load_state(&paths).unwrap();
        let second = load_state(&paths).unwrap();
        assert_eq!(first.hash_salt, second.hash_salt);
        assert_eq!(
            first.data_path.as_deref(),
            Some(root.to_string_lossy().as_ref())
        );
        assert_eq!(
            fs::metadata(root.join("state.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        fs::write(root.join("state.json"), r#"{"format":1,"hash_salt":"x"}"#).unwrap();
        assert!(load_state(&paths).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
