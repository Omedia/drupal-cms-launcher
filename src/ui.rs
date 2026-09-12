use drupal_cms_launcher::runtime::{Event, Paths, record_timing};
use gpui_kit::{
    component::{
        ActiveTheme, Disableable, Root,
        button::{Button, ButtonVariants},
    },
    *,
};
use std::{
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::mpsc::{self, Receiver},
    thread,
    time::Duration,
};

actions!(drupal_cms_launcher, [Quit, OpenSite, OpenLogs]);

struct Runner {
    child: Child,
    input: ChildStdin,
    events: Receiver<Event>,
}

impl Runner {
    fn start(paths: &Paths) -> anyhow::Result<Self> {
        let mut child = Command::new(std::env::current_exe()?)
            .arg("--supervise")
            .arg("--resources")
            .arg(&paths.resources)
            .arg("--data-dir")
            .arg(&paths.data)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let input = child.stdin.take().unwrap();
        let output = child.stdout.take().unwrap();
        let (sender, events) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                let Ok(line) = line else {
                    break;
                };
                if let Ok(event) = serde_json::from_str::<Event>(&line)
                    && sender.send(event).is_err()
                {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            input,
            events,
        })
    }

    fn send(&mut self, command: &str) {
        let _ = writeln!(self.input, "{command}");
    }
}

impl Drop for Runner {
    fn drop(&mut self) {
        // Closing stdin also stops the supervisor, which outlives the window
        // long enough to stop PHP and detach the site image.
        self.send("STOP");
    }
}

struct Launcher {
    paths: Paths,
    runner: Option<Runner>,
    phase: String,
    message: String,
    address: String,
    focus: FocusHandle,
    has_rendered: bool,
}

impl Launcher {
    fn new(paths: Paths, cx: &mut Context<Self>) -> Self {
        let mut this = Self {
            paths,
            runner: None,
            phase: "preparing".into(),
            message: "Preparing your site…".into(),
            address: String::new(),
            focus: cx.focus_handle(),
            has_rendered: false,
        };
        this.start();
        record_timing("ui.created", None);
        cx.spawn(async move |view, cx| {
            loop {
                cx.background_executor()
                    .timer(Duration::from_millis(150))
                    .await;
                if view.update(cx, |this, cx| this.poll(cx)).is_err() {
                    break;
                }
            }
        })
        .detach();
        this
    }

    fn start(&mut self) {
        self.phase = "preparing".into();
        self.message = "Preparing your site…".into();
        match Runner::start(&self.paths) {
            Ok(runner) => self.runner = Some(runner),
            Err(error) => {
                self.phase = "error".into();
                self.message = format!("Could not start the site: {error}");
            }
        }
    }

    fn poll(&mut self, cx: &mut Context<Self>) {
        let Some(runner) = &mut self.runner else {
            return;
        };
        let events: Vec<_> = runner.events.try_iter().collect();
        let mut changed = !events.is_empty();
        for event in events {
            if event.phase == "open" {
                if let Some(url) = event.url {
                    cx.open_url(&url);
                }
                continue;
            }
            if event.phase == "ready" {
                self.address = event.url.unwrap_or_default();
                record_timing("ui.ready", Some(&self.address));
                if std::env::var("DRUPAL_CMS_NO_BROWSER").as_deref() != Ok("1") {
                    runner.send("OPEN");
                }
            }
            self.phase = event.phase;
            self.message = event.message;
        }
        if let Ok(Some(status)) = runner.child.try_wait() {
            if !matches!(self.phase.as_str(), "stopped" | "error") {
                self.phase = if status.success() { "stopped" } else { "error" }.into();
                self.message = if status.success() {
                    "Your site is stopped"
                } else {
                    "The site stopped unexpectedly. Open the logs for details."
                }
                .into();
            }
            self.runner = None;
            changed = true;
        }
        if changed {
            cx.notify();
        }
    }

    fn open(&mut self) {
        if self.phase == "ready"
            && let Some(runner) = &mut self.runner
        {
            runner.send("OPEN");
        }
    }

    fn toggle(&mut self) {
        if let Some(runner) = &mut self.runner {
            runner.send("STOP");
            self.phase = "stopping".into();
            self.message = "Saving and stopping your site…".into();
        } else {
            self.start();
        }
    }

    fn show_folder(&self, logs: bool) {
        let path = if logs {
            self.paths.data.join("logs")
        } else {
            self.paths.data.join("site")
        };
        let _ = Command::new("/usr/bin/open").arg(path).spawn();
    }
}

impl Render for Launcher {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if !self.has_rendered {
            record_timing("ui.first_render", None);
            self.has_rendered = true;
        }
        let ready = self.phase == "ready";
        let stopped = self.runner.is_none();
        let failed = self.phase == "error";
        let color = if ready {
            rgb(0x27864c)
        } else if failed {
            rgb(0xc33b36)
        } else {
            rgb(0x87909d)
        };
        div()
            .size_full()
            .flex()
            .flex_col()
            .p_6()
            .gap_5()
            .bg(cx.theme().background)
            .text_color(cx.theme().foreground)
            .font_family(".AppleSystemUIFont")
            .text_sm()
            .track_focus(&self.focus)
            .on_action(cx.listener(|this, _: &OpenSite, _, _| this.open()))
            .on_action(cx.listener(|this, _: &OpenLogs, _, _| this.show_folder(true)))
            .child(
                div()
                    .flex()
                    .flex_col()
                    .gap_1()
                    .child(
                        div()
                            .text_2xl()
                            .font_weight(FontWeight::SEMIBOLD)
                            .child("Drupal CMS"),
                    )
                    .child(
                        div()
                            .text_color(cx.theme().muted_foreground)
                            .child("Your local site"),
                    ),
            )
            .child(
                div()
                    .flex_1()
                    .flex()
                    .flex_col()
                    .gap_3()
                    .child(
                        div()
                            .flex()
                            .items_center()
                            .gap_2()
                            .child(div().size(px(8.)).rounded_full().bg(color))
                            .child(
                                div()
                                    .font_weight(FontWeight::MEDIUM)
                                    .child(self.message.clone()),
                            ),
                    )
                    .child(
                        div()
                            .text_color(cx.theme().muted_foreground)
                            .child(if ready {
                                self.address.clone()
                            } else if failed {
                                "Your saved content is kept on this Mac.".into()
                            } else if stopped {
                                "Start your site whenever you want to continue.".into()
                            } else {
                                "Everything needed is included. First launch takes a little longer."
                                    .into()
                            }),
                    ),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        Button::new("open-site")
                            .primary()
                            .label("Open Drupal")
                            .disabled(!ready)
                            .on_click(cx.listener(|this, _, _, _| this.open())),
                    )
                    .child(
                        Button::new("toggle-site")
                            .label(if stopped {
                                if failed { "Try Again" } else { "Start Site" }
                            } else {
                                "Stop Site"
                            })
                            .disabled(self.phase == "stopping")
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.toggle();
                                cx.notify();
                            })),
                    ),
            )
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap_2()
                    .child(
                        Button::new("site-folder")
                            .ghost()
                            .label("Site Folder")
                            .on_click(cx.listener(|this, _, _, _| this.show_folder(false))),
                    )
                    .child(
                        Button::new("logs")
                            .ghost()
                            .label("View Logs")
                            .on_click(cx.listener(|this, _, _, _| this.show_folder(true))),
                    )
                    .child(div().flex_1())
                    .child(
                        div()
                            .text_xs()
                            .text_color(cx.theme().muted_foreground)
                            .child("Closing stops the site."),
                    ),
            )
    }
}

pub fn run(paths: Paths) {
    application().run(move |cx| {
        gpui_kit::init(cx);
        cx.bind_keys([
            KeyBinding::new("cmd-q", Quit, None),
            KeyBinding::new("cmd-o", OpenSite, None),
            KeyBinding::new("cmd-l", OpenLogs, None),
        ]);
        cx.on_action(|_: &Quit, cx| cx.quit());
        cx.set_menus(vec![Menu {
            name: "Drupal CMS Launcher".into(),
            items: vec![MenuItem::action("Quit Drupal CMS Launcher", Quit)],
            disabled: false,
        }]);
        cx.on_window_closed(|cx, _| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
        let bounds = Bounds::centered(None, size(px(540.), px(350.)), cx);
        cx.spawn(async move |cx| {
            cx.open_window(
                WindowOptions {
                    window_bounds: Some(WindowBounds::Windowed(bounds)),
                    window_min_size: Some(size(px(500.), px(320.))),
                    titlebar: Some(TitlebarOptions {
                        title: Some("Drupal CMS Launcher".into()),
                        ..Default::default()
                    }),
                    ..Default::default()
                },
                |window, cx| {
                    cx.activate(true);
                    let view = cx.new(|cx| Launcher::new(paths, cx));
                    view.update(cx, |view, cx| view.focus.focus(window, cx));
                    cx.new(|cx| Root::new(view, window, cx))
                },
            )
            .expect("Could not open the Drupal CMS Launcher window");
        })
        .detach();
    });
}
