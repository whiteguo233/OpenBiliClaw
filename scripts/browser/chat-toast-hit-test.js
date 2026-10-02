// Run with playwright-cli run-code after opening desktop Web's chat view.
// Uses real app CSS; overlays a notice exactly where a user clicks the composer.
async (page) => {
  const input = page.locator('#chatInput');
  await input.scrollIntoViewIfNeeded();
  await page.evaluate(() => {
    const input = document.querySelector('#chatInput');
    const rect = input.getBoundingClientRect();
    const toast = document.createElement('div');
    toast.id = 'toast-hit-test';
    toast.className = 'toast-item';
    toast.textContent = '卡片操作提示';
    Object.assign(toast.style, {
      position: 'fixed', left: `${rect.left}px`, top: `${rect.top}px`,
      width: `${rect.width}px`, height: `${rect.height}px`,
      boxSizing: 'border-box', padding: '0', zIndex: '9999',
    });
    document.querySelector('#toastContainer').append(toast);
    input.blur();
  });
  try {
    await input.click({ timeout: 2000 });
    if (!await input.evaluate(el => document.activeElement === el)) {
      throw new Error('Chat notice intercepted composer click');
    }
    return { passed: true };
  } finally {
    await page.evaluate(() => document.querySelector('#toast-hit-test')?.remove());
  }
}
