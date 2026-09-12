// Filled with a run-specific config by benchmark.py; no request bodies are saved.
async (page) => {
  const config = __BENCHMARK_CONFIG__;
  let phase = config.step;
  const requests = [];
  const pending = [];
  const started = new Map();
  const onRequest = request => started.set(request, { phase, at: Date.now() });
  const onFinished = request => {
    const record = started.get(request);
    if (!record) return;
    pending.push((async () => {
      const response = await request.response();
      const timing = request.timing();
      const address = request.url();
      const path = address.replace(/^https?:\/\/[^/]+/, '').split('?')[0];
      const operation = address.match(/[?&]op=([^&]*)/);
      const item = {
        phase: record.phase, method: request.method(), path,
        operation: operation ? decodeURIComponent(operation[1]) : null, status: response?.status(),
        elapsed_ms: Date.now() - record.at,
        ttfb_ms: timing.responseStart >= 0 && timing.requestStart >= 0 ? timing.responseStart - timing.requestStart : null,
        transfer_ms: timing.responseEnd >= 0 && timing.responseStart >= 0 ? timing.responseEnd - timing.responseStart : null,
      };
      if (path === '/core/install.php' && response && (response.headers()['content-type'] || '').includes('json')) {
        try {
          const body = await response.json();
          item.percentage = body.percentage;
          item.progress = typeof body.message === 'string' ? body.message.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').slice(0, 600) : null;
        } catch { /* Some installer errors return HTML instead of progress JSON. */ }
      }
      requests.push(item);
    })());
  };
  page.on('request', onRequest);
  page.on('requestfinished', onFinished);
  const measures = {};
  const measure = async (name, action) => {
    phase = name;
    const begin = Date.now();
    await action();
    measures[name] = (Date.now() - begin) / 1000;
  };
  try {
    if (config.step === 'prepare') {
      await measure('first_installer_page', async () => {
        await page.goto(config.url);
        await page.getByRole('heading', { name: 'Give your site a name', exact: true }).waitFor();
      });
      await page.getByRole('textbox', { name: 'Site name *', exact: true }).fill('Launcher Benchmark');
      await measure('save_site_name', async () => {
        await page.getByRole('button', { name: 'Next', exact: true }).click();
        await page.getByRole('heading', { name: 'Choose a site template', exact: true }).waitFor();
      });
      await page.getByRole('heading', { name: config.template, exact: true }).click();
      await measure('select_template', async () => {
        await page.getByRole('button', { name: 'Next', exact: true }).click();
        await page.getByRole('heading', { name: 'Create your account', exact: true }).waitFor();
      });
    } else if (config.step === 'install') {
      await page.getByRole('heading', { name: 'Create your account', exact: true }).waitFor();
      await page.getByRole('textbox', { name: 'Email *', exact: true }).fill('benchmark@example.test');
      await page.getByRole('textbox', { name: 'Password *', exact: true }).fill('Benchmark-Only-7392!');
      await measure('finish_to_dashboard', async () => {
        await page.getByRole('button', { name: 'Finish', exact: true }).click();
        await Promise.race([
          page.waitForURL(url => url.pathname.startsWith('/admin/'), { timeout: 240000 }),
          page.getByText('The installation has encountered an error.', { exact: false })
            .waitFor({ timeout: 240000 }).then(() => { throw new Error('Installation failed'); }),
        ]);
        await page.getByRole('heading', { name: 'Dashboard', level: 1, exact: true }).waitFor();
      });
    } else if (config.step === 'reopen') {
      await measure('installed_homepage', async () => {
        await page.goto(config.url);
        if (page.url().includes('/core/install.php') || !(await page.title()).includes('Launcher Benchmark')) {
          throw new Error('Installed site did not persist');
        }
      });
    } else { throw new Error('Unknown benchmark step'); }
    const settled = await Promise.allSettled(pending);
    const failure = settled.find(result => result.status === 'rejected');
    if (failure) throw new Error('Request timing capture failed: ' + failure.reason);
    if (!requests.length) throw new Error('No browser requests were captured');
    return { measures, requests, title: await page.title(), url: page.url() };
  } finally {
    page.off('request', onRequest);
    page.off('requestfinished', onFinished);
  }
}
