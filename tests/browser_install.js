// Run with: playwright-cli -s=cms-installer run-code --filename=tests/browser_install.js
// Only use a fresh, disposable site. The server should block outgoing internet.
async (page) => {
  if (!(await page.title()).startsWith('Give your site a name')) {
    throw new Error('Expected a fresh Drupal CMS installer; refusing to alter an existing site.');
  }
  await page.getByRole('textbox', { name: 'Site name *', exact: true }).fill('Launcher Browser Test');
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await page.getByRole('heading', { name: 'Choose a site template', exact: true }).waitFor();
  await page.getByRole('heading', { name: 'Byte', exact: true }).click();
  await page.getByRole('button', { name: 'Next', exact: true }).click();
  await page.getByRole('heading', { name: 'Create your account', exact: true }).waitFor();
  await page.getByRole('textbox', { name: 'Email *', exact: true }).fill('launcher-test@example.test');
  await page.getByRole('textbox', { name: 'Password *', exact: true }).fill('Local-Test-Only-7392!');
  await page.getByRole('button', { name: 'Finish', exact: true }).click();
  await Promise.race([
    page.waitForURL(url => url.pathname.startsWith('/admin/'), { timeout: 180000 }),
    page.getByText('The installation has encountered an error.', { exact: false })
      .waitFor({ timeout: 180000 }).then(() => { throw new Error('Drupal CMS browser installation failed; inspect the batch error.'); }),
  ]);
  return { installed: true, url: page.url(), title: await page.title() };
}
