/* Progressive media controls for the original homepage. No video is loaded until played. */
(() => {
  const videos = [...document.querySelectorAll('.walkthrough-card video, .tour-details video')];
  const tour = document.getElementById('demo');
  const covers = videos.map(video => {
    const shell = document.createElement('div');
    shell.className = 'media-start-shell';
    video.before(shell);
    shell.append(video);
    const originalTabIndex = video.getAttribute('tabindex');
    video.tabIndex = -1;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'media-start-cover';
    const poster = document.createElement('img');
    poster.src = video.poster;
    poster.alt = '';
    const label = document.createElement('span');
    label.className = 'media-start-label';
    button.append(poster, label);
    shell.append(button);
    button.addEventListener('click', () => {
      button.disabled = true;
      video.play().catch(() => { button.disabled = false; });
    });
    video.addEventListener('play', () => {
      const restoreFocus = document.activeElement === button;
      button.remove();
      if (originalTabIndex === null) video.removeAttribute('tabindex');
      else video.setAttribute('tabindex', originalTabIndex);
      if (restoreFocus) video.focus();
    }, {once: true});
    return {video, button, label};
  });

  function syncMediaLanguage() {
    const language = document.documentElement.lang.split('-')[0];
    videos.forEach(video => video.querySelectorAll('track').forEach(track => {
      const selected = track.srclang.split('-')[0] === language;
      track.default = selected;
      track.track.mode = selected ? 'showing' : 'disabled';
    }));
    covers.forEach(({video, button, label}) => {
      label.textContent = language === 'en' ? '▶ Play video' : '▶ 播放视频';
      button.setAttribute('aria-label', `${language === 'en' ? 'Play' : '播放'}: ${video.getAttribute('aria-label')}`);
    });
  }

  function revealTour() {
    if (location.hash !== '#demo') return;
    tour.open = true;
    requestAnimationFrame(() => tour.scrollIntoView({block: 'start', behavior: 'instant'}));
  }
  videos.forEach(video => video.addEventListener('loadedmetadata', syncMediaLanguage));
  tour.addEventListener('toggle', () => { if (!tour.open) tour.querySelector('video').pause(); });
  window.addEventListener('openbiliclaw-language', syncMediaLanguage);
  window.addEventListener('hashchange', revealTour);
  syncMediaLanguage();
  revealTour();
})();
