/* Static Chinese content remains usable when scripting or storage is unavailable. */
(() => {
  const english = {
    skip: "Skip to content", navLabel: "Main navigation", navPreview: "Product", navHow: "How it works", navDocs: "Docs",
    getStarted: "Get started", heroEyebrow: "OPEN SOURCE · LOCAL FIRST · YOUR DISCOVERY AGENT",
    heroLine1: "Good content.", heroLine2: "Found for you.",
    heroLead: "Running on your computer, OpenBiliClaw discovers content across Bilibili, Xiaohongshu, YouTube and more. It explains each recommendation and learns from your feedback.",
    seeProduct: "See the product", heroNote: "Data stays on your machine by default. You choose the model service.",
    previewLabel: "It starts with a recommendation", previewControls: "Choose a product screenshot",
    tabHome: "Recommendations", tabFeedback: "Reasons & feedback", tabProfile: "Interest profile",
    captionHome: "Desktop recommendations · Actual screenshot; content varies with your profile",
    captionFeedback: "Recommendation reasons and feedback · Actual browser extension screenshot",
    captionProfile: "Your interest profile · Actual desktop screenshot",
    altHome: "OpenBiliClaw desktop with recommendation reasons, content cards and feedback controls",
    altFeedback: "Browser extension showing why a video was recommended and a written feedback form",
    altProfile: "OpenBiliClaw desktop interest profile and preferences",
    viewOriginal: "Full-size image", sourcesLabel: "Bring your interests together, across platforms.", sourcesAria: "Content sources",
    bilibili: "Bilibili", xiaohongshu: "Xiaohongshu", douyin: "Douyin", zhihu: "Zhihu", weibo: "Weibo",
    sourcesNote: "Discovery, sign-in and initialization support vary by source. Connect the ones you use.",
    whyEyebrow: "DISCOVER. UNDERSTAND. REFINE.", whyTitle: "Good recommendations\ncome with a reason.",
    featureFindTitle: "Let it do the finding", featureFindText: "Start with your interests. Search across platforms, follow related content and explore subjects you haven't tried yet.",
    featureFindDetail: "Search · Related discoveries · Multiple sources",
    featureReasonTitle: "Understand the connection", featureReasonText: "Each recommendation includes an explanation. See how a piece of content connects to your interests before deciding to open it.",
    featureReasonDetail: "Clear reasons · Your interests · Your choice",
    featureLearnTitle: "Give feedback that matters", featureLearnText: "Like it, dismiss it, or chat about what you want to see. Feedback informs future learning, and you can inspect and adjust your profile.",
    featureLearnDetail: "Explicit feedback · Conversation · Profile edits",
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
  const links = [...document.querySelectorAll("[data-href-en]")].map(node => ({node, original: node.getAttribute("href")}));
  const metaNodes = [...document.querySelectorAll('meta[name="description"], meta[property^="og:"], meta[name^="twitter:"]')].map(node => ({node, original: node.content}));
  const originalTitle = document.title;
  const toggle = document.getElementById("language-toggle");
  const image = document.getElementById("preview-image");
  const caption = document.getElementById("preview-caption");
  const fullSize = document.getElementById("preview-original");
  const previews = {
    home: {file: "desktop-home.png", width: 1600, height: 1000, alt: "altHome", caption: "captionHome", zhAlt: image.alt, zhCaption: caption.textContent},
    feedback: {file: "screenshot-recommend-feedback.png", width: 808, height: 1060, alt: "altFeedback", caption: "captionFeedback", zhAlt: "浏览器插件里的推荐理由与文字反馈表单", zhCaption: "推荐理由与反馈 · 浏览器插件实际运行截图"},
    profile: {file: "desktop-profile.png", width: 1600, height: 1000, alt: "altProfile", caption: "captionProfile", zhAlt: "OpenBiliClaw 桌面端的兴趣画像与偏好", zhCaption: "兴趣画像 · 桌面端实际运行截图"},
  };
  let language = "zh";
  let activePreview = "home";
  function showPreview(key) {
    const preview = previews[key];
    if (!preview) return;
    activePreview = key;
    image.parentElement.dataset.format = key === "feedback" ? "portrait" : "landscape";
    image.src = `images/${preview.file}`;
    image.width = preview.width;
    image.height = preview.height;
    image.alt = language === "en" ? english[preview.alt] : preview.zhAlt;
    caption.textContent = language === "en" ? english[preview.caption] : preview.zhCaption;
    fullSize.href = image.getAttribute("src");
    document.querySelectorAll("[data-preview]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.preview === key)));
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
    links.forEach(({node, original}) => node.setAttribute("href", isEnglish ? node.dataset.hrefEn : original));
    document.title = isEnglish ? "OpenBiliClaw — Your local-first content discovery agent" : originalTitle;
    metaNodes.forEach(({node, original}) => {
      const name = node.getAttribute("property") || node.getAttribute("name");
      let value = original;
      if (isEnglish) {
        if (["og:title", "twitter:title"].includes(name)) value = document.title;
        if (["description", "og:description", "twitter:description"].includes(name)) value = english.heroLead;
        if (name === "og:locale") value = "en_US";
        if (name === "og:locale:alternate") value = "zh_CN";
        if (["og:image", "og:image:secure_url", "twitter:image"].includes(name)) value = original.replace("social-preview-zh.png", "social-preview-en.png");
        if (["og:image:alt", "twitter:image:alt"].includes(name)) value = "OpenBiliClaw, a local-first cross-platform content discovery agent";
      }
      node.content = value;
    });
    toggle.textContent = isEnglish ? "中文" : "EN";
    toggle.setAttribute("aria-label", isEnglish ? "切换到中文" : "Switch to English");
    showPreview(activePreview);
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
  document.querySelectorAll("[data-preview]").forEach(button => button.addEventListener("click", () => showPreview(button.dataset.preview)));
  toggle.hidden = false;
  document.getElementById("preview-controls").hidden = false;
  document.getElementById("preview-controls").parentElement.classList.add("has-controls");
})();
