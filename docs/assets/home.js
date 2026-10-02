/* Static Chinese content remains usable when scripting or storage is unavailable. */
(() => {
  const english = {
    skip: "Skip to content", navLabel: "Main navigation", navPreview: "Product", navHow: "How it works", navDocs: "Docs",
    getStarted: "Get started", heroEyebrow: "OPEN SOURCE · RUNS LOCALLY · BUILT FOR YOU ALONE",
    heroLine1: "A self-evolving", heroLine2: "content discovery agent",
    heroLead: "Running on your computer, it deepens its understanding through your activity, feedback and conversations, then searches across platforms. It explains each recommendation and learns from your feedback.",
    seeProduct: "Watch the product tour", demoTitle: "See OpenBiliClaw in action.", tourLabel: "70-second product tour · 中文 / English captions",
    demoVideoLabel: "OpenBiliClaw product tour showing desktop, browser extension, Mobile Web and key features",
    demoFallback: "Your browser cannot play this video. Use the download link below to watch it.", demoDownload: "Download video",
    tourCaption: "Browse recommendations · Read reasons · Profiles & exploration · Chat · Save across clients",
    tourContext: "Edited from real screen recordings and existing repository screenshots.", demoTranscript: "Transcript & media notes",
    screensEyebrow: "CHOOSE YOUR PREFERRED INTERFACE", screensTitle: "Browse on desktop, in the extension, or on your phone.",
    screensIntro: "Connect to the same backend to read recommendations, give feedback and save content.",
    surfaceDesktopAlt: "Desktop Web recommendations showing cross-platform content cards and recommendation reasons",
    surfaceDesktopTitle: "Desktop Web", surfaceDesktopText: "Browse on a larger screen, expand reasons, search and filter.", surfaceDesktopLink: "Watch desktop actions ↓",
    surfaceExtensionAlt: "OpenBiliClaw browser extension showing recommendation cards and feedback controls",
    surfaceExtensionTitle: "Browser extension", surfaceExtensionText: "Read recommendations inside your browser, with feedback and library access.", surfaceExtensionLink: "Watch extension actions ↓",
    surfaceMobileAlt: "Mobile Web recommendations showing reasons, Like and save controls",
    surfaceMobileTitle: "Mobile Web", surfaceMobileText: "Browse, tap Like and open your shared library in a phone browser.", surfaceMobileLink: "Watch mobile actions ↓",
    desktopDemoTitle: "Desktop recording: reasons → watch later → library (25 seconds)", desktopDemoVideoLabel: "Real desktop recording of browsing recommendations and saving content",
    desktopDemoCaption: "25 seconds · Real screen recording · Bilingual captions",
    extensionDemoTitle: "Extension recording: browse recommendations → read reasons → library (18 seconds)", extensionDemoVideoLabel: "Real browser extension recording of recommendations, reasons and the library",
    extensionDemoCaption: "18 seconds · Extension page recording · Bilingual captions",
    mobileDemoTitle: "Mobile recording: Like → shared library (20 seconds)", mobileDemoVideoLabel: "Real Mobile Web recording of Like feedback and the shared library",
    mobileDemoCaption: "20 seconds · Mobile Web recording · Bilingual captions",
    featuresEyebrow: "MORE THAN A RECOMMENDATION LIST", featuresTitle: "Profiles, exploration, conversation and a library.", imageHint: "Click an image for full size ↗",
    profileAlt: "User profile screen showing values and personality traits", profileTitle: "Inspect and adjust your profile", profileText: "See how it understands you. Model inferences can be checked and corrected.",
    styleAlt: "Content style screen showing preferences for formats and styles of expression", styleTitle: "Describe your taste in content", styleText: "Beyond topics, it considers preferred formats, style and depth.",
    probeAlt: "Interest probes screen showing new directions and ways to respond", probeTitle: "Explore new directions", probeText: "Confirm, reject, postpone or discuss suggested interests.",
    chatAlt: "Mobile Web conversation screen showing chat messages and the input field", chatTitle: "Talk about what you want to see", chatText: "Describe specific preferences, with feedback and conversation informing future understanding.",
    featureImageNote: "These are existing repository screenshots. Their interface version may differ from the recordings.",
    libraryAlt: "Desktop library showing recommendation items saved to watch later", libraryEyebrow: "SAVE SOMETHING FOR LATER", libraryTitle: "Watch later, favorites and history.",
    libraryText: "Save a recommendation worth keeping. Desktop and mobile connect to the same backend, so you can return to the same records.", libraryCaption: "The screenshot shows Watch Later in the desktop library.",
    sourcesLabel: "Discover across platforms.", sourcesAria: "Content sources", bilibili: "Bilibili", xiaohongshu: "Xiaohongshu", douyin: "Douyin", zhihu: "Zhihu", weibo: "Weibo",
    sourcesNote: "Discovery, sign-in and initialization support vary by source. Connect the ones you use.",
    whyEyebrow: "WHY OPENBILICLAW", whyTitle: "Put recommendations back on the user's side.",
    whyIntro: "Built for you alone, connecting interests from different platforms in a system of your own.",
    featureProfileTitle: "Understand the person first", featureProfileText: "Authorized activity, feedback and conversation form a profile that informs search, evaluation and recommendations.",
    featureFindTitle: "Find across platforms", featureFindText: "Search actively, follow related content and explore adjacent interests. Recommendations include reasons, and the choice stays with you.",
    featureLearnTitle: "Keep learning through use", featureLearnText: "Likes, dismissals and specific corrections inform later learning. You can inspect and edit your profile; AI rebuilds do not simply overwrite manual corrections.",
    learningLoopLabel: "Continuous learning loop", loopSignals: "Activity signals", loopProfile: "Deepen the profile", loopDiscover: "Discover actively", loopRecommend: "Explain recommendations", loopFeedback: "Feedback & conversation ↺",
    documentationLabel: "Learn more", architectureLink: "System architecture ↗", profileUsageLink: "How profiles inform recommendations ↗", documentationLink: "All documentation ↗",
    howEyebrow: "HOW IT WORKS", howTitle: "Your choice of models.\nYour computer at the center.",
    howIntro: "The extension connects your platforms. A local backend handles understanding, discovery and recommendations. Desktop, mobile and other clients share that service.",
    flowAria: "How OpenBiliClaw works and where data goes", flowSourceLabel: "CONTENT & SIGNALS", flowSourceTitle: "Your connected sources",
    flowSourceText: "Public content, authorized personal signals and your feedback",
    flowLocalLabel: "ON YOUR COMPUTER", flowLocalTitle: "OpenBiliClaw backend", flowLocalText: "Understand → Discover → Recommend",
    flowStorage: "Profiles, recommendations and history stored locally", flowClientLabel: "YOUR PREFERRED INTERFACE", flowClientTitle: "Browser, desktop & phone",
    flowClientText: "Read, chat and give feedback to inform the next round",
    modelLabel: "Backend ↔ Your model service", modelText: "When you use a cloud LLM or embedding service, necessary content is sent to your chosen provider. Local storage does not mean all inference happens on your machine.",
    privacyLink: "Data & privacy", installEyebrow: "GET STARTED", installTitle: "Meet your first recommendations.",
    installIntro: "Start with the desktop installer. You'll need a computer, a working model service, and at least one content source that can provide interest signals.",
    step1Title: "Install the desktop backend", step1Text: "Choose the macOS or Windows installer in Releases. On Linux, use Docker or install from source.", downloadDesktop: "Get the desktop installer",
    step2Title: "Add the browser extension", step2Text: "The extension connects platform sessions and activity signals. Chrome Store installs update automatically; see the guide for other browsers.", downloadExtension: "Add to Chrome",
    step3Title: "Connect models & sources", step3Text: "Open setup to configure your LLM, embedding service and content sources. Sign in or enter a public username as required by each source.",
    step4Title: "Initialize, then explore", step4Text: "Wait for service checks, signal import and the first discovery round to complete. Open the recommendations page, then refine your interests as you go.",
    installNote: "Desktop installers are experimental and may trigger first-launch security prompts. Slim packages download the embedding model on first use; with-embedding packages include it. Both still need a separately configured LLM.",
    installGuide: "Installation guide & troubleshooting", otherPaths: "Other ways to install", aiInstall: "Install with an AI assistant", sourceInstall: "From source", mirrors: "China download mirrors",
    clientsEyebrow: "MORE WAYS TO USE IT", clientsTitle: "One backend. Your preferred interface.", clientsText: "Beyond the desktop web app and extension, access your recommendations on a phone or in an existing agent workflow. Every client connects to a running backend.",
    mobileWeb: "Mobile browser", nativeApp: "Flutter native client", previewBadge: "Preview ↗", clientPlugin: "Client plugin ↗", integrationGuide: "Integration guide ↗",
    faqTitle: "A few things\nbefore you begin.", moreFaq: "More questions & answers",
    faq1Title: "Is the browser extension enough?", faq1Text: "You also need the backend. The extension connects content platforms and provides an interface, while the backend handles profiles, discovery, recommendations and storage. The desktop installer runs that backend for you.",
    faq2Title: "Does it cost anything? Do I need an API key?", faq2Text: "OpenBiliClaw is open source under the MIT license. You need a working LLM service, usually with your own API key. Model costs depend on your provider and usage. For local embedding, you can use Ollama.",
    faq3Title: "Where does my data go?", faq3Text: "Profiles and history are stored on the backend computer by default. The extension does not upload data to project-operated servers. Cloud model calls send necessary content to your chosen provider. See the privacy policy for self-hosted and remote access details.",
    faq4Title: "Must I sign in to every platform?", faq4Text: "No. Start with the sources you use. Some support public discovery or initialization using a public username. Follow setup to sign in and authorize sources that need personal history, favorites or similar signals.",
    closingEyebrow: "BUILT WITH THE COMMUNITY", closingTitle: "Make your next discovery a better fit.", closingText: "Try it, report an issue, contribute code, or support the project on GitHub.", viewGithub: "View on GitHub",
    footerNav: "More resources", changelog: "Changelog", feedback: "Report an issue", community: "Community", privacy: "Privacy",
  };
  const nodes = [...document.querySelectorAll("[data-i18n]")].map(node => ({node, original: node.innerHTML}));
  const ariaNodes = [...document.querySelectorAll("[data-i18n-aria]")].map(node => ({node, original: node.getAttribute("aria-label")}));
  const altNodes = [...document.querySelectorAll("[data-i18n-alt]")].map(node => ({node, original: node.alt}));
  const structuredDataNode = document.querySelector('script[type="application/ld+json"]');
  const structuredData = JSON.parse(structuredDataNode.textContent);
  const links = [...document.querySelectorAll("[data-href-en]")].map(node => ({node, original: node.getAttribute("href")}));
  const metaNodes = [...document.querySelectorAll('meta[name="description"], meta[property^="og:"], meta[name^="twitter:"]')].map(node => ({node, original: node.content}));
  const originalTitle = document.title;
  const toggle = document.getElementById("language-toggle");
  const demoVideos = [...document.querySelectorAll(".demo-player video")];
  // Keep a clear, keyboard-accessible poster until playback is requested.
  // The original video node and native controls remain in place throughout.
  const playCovers = demoVideos.map(video => {
    const shell = document.createElement("div");
    shell.className = "media-start-shell";
    video.before(shell);
    shell.append(video);
    const originalTabIndex = video.getAttribute("tabindex");
    video.tabIndex = -1;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "media-start-cover";
    const poster = document.createElement("img");
    poster.src = video.poster;
    poster.alt = "";
    const label = document.createElement("span");
    label.className = "media-start-label";
    button.append(poster, label);
    shell.append(button);
    button.addEventListener("click", () => {
      button.disabled = true;
      video.play().catch(() => { button.disabled = false; });
    });
    video.addEventListener("play", () => {
      const restoreFocus = document.activeElement === button;
      button.remove();
      if (originalTabIndex === null) video.removeAttribute("tabindex");
      else video.setAttribute("tabindex", originalTabIndex);
      if (restoreFocus) video.focus();
    }, {once: true});
    return {video, button, label};
  });
  const recordingDemos = [...document.querySelectorAll(".recording-demo")];
  let language = "zh";
  function setDemoCaptionLanguage(videos = demoVideos) {
    // Only caption selection changes: keep the media node, source and playback state intact.
    videos.forEach(video => {
      video.querySelectorAll("track").forEach(track => {
        const selected = track.srclang.split("-")[0] === language;
        track.default = selected;
        track.track.mode = selected ? "showing" : "disabled";
      });
    });
  }
  function revealRecording() {
    const recording = recordingDemos.find(item => `#${item.id}` === location.hash);
    if (!recording) return;
    recording.open = true;
    requestAnimationFrame(() => recording.scrollIntoView({block: "start", behavior: "instant"}));
  }
  function setLanguage(value) {
    language = value === "en" ? "en" : "zh";
    const isEnglish = language === "en";
    document.documentElement.lang = isEnglish ? "en" : "zh-CN";
    nodes.forEach(({node, original}) => {
      if (isEnglish && english[node.dataset.i18n] !== undefined) node.textContent = english[node.dataset.i18n];
      else node.innerHTML = original;
    });
    ariaNodes.forEach(({node, original}) => node.setAttribute("aria-label", isEnglish ? english[node.dataset.i18nAria] : original));
    altNodes.forEach(({node, original}) => { node.alt = isEnglish ? english[node.dataset.i18nAlt] : original; });
    links.forEach(({node, original}) => node.setAttribute("href", isEnglish ? node.dataset.hrefEn : original));
    document.title = isEnglish ? "OpenBiliClaw — A self-evolving cross-platform content discovery agent" : originalTitle;
    metaNodes.forEach(({node, original}) => {
      const name = node.getAttribute("property") || node.getAttribute("name");
      let value = original;
      if (isEnglish) {
        if (["og:title", "twitter:title"].includes(name)) value = document.title;
        if (["description", "og:description", "twitter:description"].includes(name)) value = english.heroLead;
        if (name === "og:locale") value = "en_US";
        if (name === "og:locale:alternate") value = "zh_CN";
        if (["og:image", "og:image:secure_url", "twitter:image"].includes(name)) value = original.replace("social-preview-zh.png", "social-preview-en.png");
        if (["og:image:alt", "twitter:image:alt"].includes(name)) value = "OpenBiliClaw social preview";
      }
      node.content = value;
    });
    const localizedData = structuredClone(structuredData);
    if (isEnglish) {
      const software = localizedData["@graph"].find(item => item["@type"] === "SoftwareApplication");
      software.description = english.heroLead;
      software.alternateName = ["OpenBiliClaw self-evolving content discovery agent"];
      localizedData["@graph"].find(item => item["@type"] === "WebSite").inLanguage = "en";
    }
    structuredDataNode.textContent = JSON.stringify(localizedData);
    playCovers.forEach(({video, button, label}) => {
      label.textContent = isEnglish ? "▶ Play video" : "▶ 播放视频";
      button.setAttribute("aria-label", `${isEnglish ? "Play" : "播放"}: ${video.getAttribute("aria-label")}`);
    });
    toggle.textContent = isEnglish ? "中文" : "EN";
    toggle.setAttribute("aria-label", isEnglish ? "切换到中文" : "Switch to English");
    setDemoCaptionLanguage();
  }
  let savedLanguage;
  try { savedLanguage = localStorage.getItem("openbiliclaw-home-lang"); } catch { /* Storage is optional. */ }
  const requestedLanguage = new URLSearchParams(location.search).get("lang");
  setLanguage(["zh", "en"].includes(requestedLanguage) ? requestedLanguage : savedLanguage);
  toggle.addEventListener("click", () => {
    setLanguage(language === "zh" ? "en" : "zh");
    try { localStorage.setItem("openbiliclaw-home-lang", language); } catch { /* Keep the page usable. */ }
    try {
      const url = new URL(location.href);
      url.searchParams.set("lang", language);
      history.replaceState(null, "", url);
    } catch { /* Language switching also works in a local file preview. */ }
  });
  demoVideos.forEach(video => video.addEventListener("loadedmetadata", () => setDemoCaptionLanguage([video])));
  recordingDemos.forEach(recording => recording.addEventListener("toggle", () => {
    if (!recording.open) recording.querySelector("video").pause();
  }));
  window.addEventListener("hashchange", revealRecording);
  revealRecording();
  toggle.hidden = false;
})();
