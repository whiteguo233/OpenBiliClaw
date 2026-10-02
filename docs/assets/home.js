/* Static Chinese content remains usable when scripting or storage is unavailable. */
(() => {
  const english = {
    skip: "Skip to content", navLabel: "Main navigation", navPreview: "Product", navHow: "How it works", navDocs: "Docs",
    getStarted: "Get started", heroEyebrow: "OPEN SOURCE · LOCAL FIRST · A CONTENT COMPANION YOU CAN TEACH",
    heroLine1: "Less of the same.", heroLine2: "More of what fits you.",
    heroLead: "Running on your computer, OpenBiliClaw turns your interests into a profile you can inspect and correct. It actively searches across platforms, explains each recommendation and learns from your feedback.",
    seeProduct: "Watch the walkthrough", heroNote: "Data stays on your machine by default. You choose the model service.",
    painAria: "Common frustrations and the control you gain",
    pain1Title: "Lots of scrolling. Little you actually want.", pain1Text: "Describe your topic, depth and taste so discovery starts with your needs.",
    pain2Title: "The same research, repeated on every platform.", pain2Text: "Use one interest profile to find videos, articles, discussions and open-source projects.",
    pain3Title: "One click, followed by more of the same.", pain3Text: "Inspect what it misunderstood. Edit your profile or explain why something missed the mark.",
    previewLabel: "It starts with a recommendation", previewControls: "Choose a product screenshot",
    tabHome: "Recommendations", tabLibrary: "Library", tabMobile: "Mobile",
    captionHome: "Desktop recommendations · Existing real recommendations in an isolated recording environment",
    captionLibrary: "Desktop library · Find the content saved for later",
    captionMobile: "Mobile Web · Recommendations from the same recording backend",
    altHome: "OpenBiliClaw desktop showing existing recommendations and their reasons in an isolated recording environment",
    altLibrary: "OpenBiliClaw desktop library showing content saved to watch later",
    altMobile: "OpenBiliClaw Mobile Web showing recommendations from the same backend",
    demoEyebrow: "A REAL WALKTHROUGH", demoTitle: "From a recommendation\nto your watch-later list.",
    demoSummary: "Read why a piece of content was recommended, save it for later, and find it in your library. Press play to follow along.",
    demoVideoLabel: "Screen recording of browsing a recommendation and saving it for later",
    demoFallback: "Your browser cannot play this video. Use the download link below to watch it.",
    demoCaption: "25 seconds · Press play · 中文 / English captions", demoDownload: "Download video", demoStepsLabel: "Walkthrough steps",
    demoStep1: "Read the recommendation reason", demoStep2: "Add it to watch later", demoStep3: "Find it in the library",
    demoContext: "Existing real recommendations in an isolated recording environment, demonstrating browsing and saving locally. No model was connected during recording; live generation and learning are not shown.",
    demoTranscript: "Transcript & recording notes",
    mobileDemoTitle: "On your phone: feedback and a shared library",
    mobileDemoVideoLabel: "Screen recording of liking a recommendation on mobile and opening the shared library",
    mobileDemoCaption: "20 seconds · Press play · Bilingual captions", mobileDemoStepsLabel: "Mobile walkthrough steps",
    mobileDemoStep1: "Tap Like to submit feedback", mobileDemoStep2: "Open the library to find the item saved on desktop",
    mobileDemoContext: "The phone and desktop connect to the same recording backend. This shows feedback submission and the shared library, not an updated profile or new recommendations.",
    viewOriginal: "Full-size image", sourcesLabel: "Bring your interests together, across platforms.", sourcesAria: "Content sources",
    bilibili: "Bilibili", xiaohongshu: "Xiaohongshu", douyin: "Douyin", zhihu: "Zhihu", weibo: "Weibo",
    sourcesNote: "Discovery, sign-in and initialization support vary by source. Connect the ones you use.",
    whyEyebrow: "UNDERSTAND ME → FIND FOR ME → LEARN FROM MY CORRECTIONS", whyTitle: "Have a say in\nhow it understands you.",
    whyIntro: "A click rarely captures your real interests. OpenBiliClaw makes its understanding visible in a profile you can inspect, discuss and edit, then use for future discovery and recommendations.",
    featureProfileTitle: "See how it sees you", featureProfileText: "Authorized history, favorites and feedback help form a profile. Inspect your interests and content preferences to see whether it understands what you actually enjoy.",
    featureProfileDetail: "Your corrections persist; AI profile rebuilds do not simply overwrite them.",
    featureFindTitle: "Send your taste along", featureFindText: "Your profile informs search queries, candidate evaluation and recommendation reasons. It also explores related content and adjacent topics, proposing new interest directions for you to confirm.",
    featureFindDetail: "Every recommendation gives a reason so you can judge the fit.",
    featureLearnTitle: "Explain what missed the mark", featureLearnText: "Like, dismiss or block an author, or describe specific preferences in conversation. Your corrections inform your profile and future discovery, giving you more ways to express yourself than clicks alone.",
    featureLearnDetail: "Likes and dismissals are soft signals; use profile edits or conversation for lasting preferences.",
    profileEyebrow: "A PROFILE YOU CAN INSPECT", profileTitle: "“Understands you” should be visible.",
    profileText: "Your profile is a correctable model inference. The page shows inferred traits and preferences, with an editing option so you can correct misunderstandings or describe changing interests.",
    profileLink: "How the profile informs recommendations", profileAlt: "Existing repository screenshot of the OpenBiliClaw profile, showing user traits and an Edit profile button",
    profileCaption: "Existing repository profile screenshot · Different version from the walkthroughs · Click for full size",
    scenariosEyebrow: "ILLUSTRATIVE USE CASES", scenariosTitle: "Turn “find something good”\ninto personal preferences.",
    scenariosIntro: "These are examples of needs you can express, showing how the product might fit your routine. They are not generated results or promises of an outcome.",
    scenario1Label: "ILLUSTRATIVE USE CASE / LEARN A SKILL", scenario1Title: "You want to play guitar, not keep shopping for one.",
    scenario1Request: "“I'm a beginner. Find structured practice and clear technique tutorials, with less gear promotion.”",
    scenario1Text: "Explain both your learning stage and the style you enjoy. Read recommendation reasons to judge the fit, then correct what missed the mark.",
    scenario2Label: "ILLUSTRATIVE USE CASE / RESEARCH ACROSS SOURCES", scenario2Title: "Researching local AI tools, from how they work to trying them yourself.",
    scenario2Request: "“Find explanations and in-depth articles about local AI tools, along with open-source projects I can try.”",
    scenario2Text: "Connect the sources you need and let the same interest profile inform different types of discovery, without explaining your interests from scratch on every platform.",
    scenario3Label: "ILLUSTRATIVE USE CASE / CHANGING INTERESTS", scenario3Title: "What you enjoyed before isn't what you want today.",
    scenario3Request: "“Less gameplay lately, more photography composition. I want to understand why a frame is arranged that way.”",
    scenario3Text: "Use explicit feedback, conversation and profile edits to express the change and give future discovery a chance to catch up.",
    fitEyebrow: "IS IT A FIT FOR YOU?", fitTitle: "Willing to teach it.\nReady for more control.",
    fitText: "If you explore across platforms, have lasting interests and are willing to spend some time connecting models and sources and correcting your profile, OpenBiliClaw is worth trying.",
    fitEffort: "Discovery needs a running backend. Cloud model costs depend on your provider and usage; local models need suitable hardware. Results depend on source availability, interest signals and model capabilities.",
    fitNote: "It provides its own recommendation interface; it does not change the native feeds on Bilibili or other platforms. The project is under active development, with initial setup and ongoing feedback part of the experience.",
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
  const image = document.getElementById("preview-image");
  const caption = document.getElementById("preview-caption");
  const fullSize = document.getElementById("preview-original");
  const demoVideos = [...document.querySelectorAll(".demo-player video")];
  const mobileDemo = document.getElementById("mobile-demo");
  const previews = {
    home: {file: "live-desktop-home.jpg", width: 1440, height: 960, alt: "altHome", caption: "captionHome", zhAlt: image.alt, zhCaption: caption.textContent},
    library: {file: "live-desktop-library.jpg", width: 1440, height: 960, alt: "altLibrary", caption: "captionLibrary", zhAlt: "OpenBiliClaw 桌面内容库：已加入稍后再看的内容", zhCaption: "桌面内容库 · 找到刚刚保存的稍后再看内容"},
    mobile: {file: "live-mobile-recommend.jpg", width: 430, height: 932, alt: "altMobile", caption: "captionMobile", zhAlt: "OpenBiliClaw 移动端 Web：来自同一后端的推荐内容", zhCaption: "移动端 Web · 连接同一个录制后端查看推荐"},
  };
  let language = "zh";
  let activePreview = "home";
  function showPreview(key) {
    const preview = previews[key];
    if (!preview) return;
    activePreview = key;
    image.parentElement.dataset.format = key === "mobile" ? "portrait" : "landscape";
    image.src = `images/${preview.file}`;
    image.width = preview.width;
    image.height = preview.height;
    image.alt = language === "en" ? english[preview.alt] : preview.zhAlt;
    caption.textContent = language === "en" ? english[preview.caption] : preview.zhCaption;
    fullSize.href = image.getAttribute("src");
    document.querySelectorAll("[data-preview]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.preview === key)));
  }
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
  function revealMobileDemo() {
    if (location.hash !== "#mobile-demo") return;
    mobileDemo.open = true;
    requestAnimationFrame(() => mobileDemo.scrollIntoView({block: "start", behavior: "instant"}));
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
    document.title = isEnglish ? "OpenBiliClaw — A cross-platform content companion you can teach" : originalTitle;
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
      software.alternateName = ["OpenBiliClaw cross-platform AI content companion"];
      localizedData["@graph"].find(item => item["@type"] === "WebSite").inLanguage = "en";
    }
    structuredDataNode.textContent = JSON.stringify(localizedData);
    toggle.textContent = isEnglish ? "中文" : "EN";
    toggle.setAttribute("aria-label", isEnglish ? "切换到中文" : "Switch to English");
    showPreview(activePreview);
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
  document.querySelectorAll("[data-preview]").forEach(button => button.addEventListener("click", () => showPreview(button.dataset.preview)));
  demoVideos.forEach(video => video.addEventListener("loadedmetadata", () => setDemoCaptionLanguage([video])));
  mobileDemo.addEventListener("toggle", () => {
    if (!mobileDemo.open) mobileDemo.querySelector("video").pause();
  });
  window.addEventListener("hashchange", revealMobileDemo);
  revealMobileDemo();
  toggle.hidden = false;
  document.getElementById("preview-controls").hidden = false;
  document.getElementById("preview-controls").parentElement.classList.add("has-controls");
})();
