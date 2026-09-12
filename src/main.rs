mod ui;

use drupal_cms_launcher::runtime::{Paths, supervisor_entry};

fn main() {
    drupal_cms_launcher::runtime::record_timing("process.main", None);
    let mut paths = match Paths::discover() {
        Ok(paths) => paths,
        Err(error) => {
            eprintln!("{error:#}");
            std::process::exit(1);
        }
    };
    let mut supervisor = false;
    let mut arguments = std::env::args_os().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.to_str() {
            Some("--supervise") => supervisor = true,
            Some("--data-dir") => {
                paths.data = arguments.next().expect("--data-dir needs a path").into()
            }
            Some("--resources") => {
                paths.resources = arguments.next().expect("--resources needs a path").into()
            }
            Some("--help") => {
                println!(
                    "Drupal CMS Launcher [--data-dir PATH] [--resources PATH]\nInternal supervisor: --supervise (stdin: OPEN or STOP; stdout: JSON events)"
                );
                return;
            }
            _ => {
                eprintln!("Unknown argument: {}", argument.to_string_lossy());
                std::process::exit(2);
            }
        }
    }
    if supervisor {
        std::process::exit(supervisor_entry(paths));
    }
    ui::run(paths);
}
