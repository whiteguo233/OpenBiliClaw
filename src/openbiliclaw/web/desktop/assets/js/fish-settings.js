/**
 * Blue Fish Lab settings panel (desktop web).
 *
 * Self-contained on purpose: it talks to the `/api/fish/*` endpoints and
 * owns its own save button, so the global `/api/config` save flow never has
 * to know about lab-only fields or the fish's separate cookie.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const el = {
    enabled: $("fishEnabled"),
    dryRun: $("fishDryRun"),
    statusLine: $("fishStatusLine"),
    cookie: $("fishCookie"),
    saveAccount: $("fishSaveAccountBtn"),
    qrLogin: $("fishQrLoginBtn"),
    qrBox: $("fishQrBox"),
    qrImage: $("fishQrImage"),
    qrStatus: $("fishQrStatus"),
    clearAccount: $("fishClearAccountBtn"),
    accountStatus: $("fishAccountStatus"),
    personaName: $("fishPersonaName"),
    personaPrompt: $("fishPersonaPrompt"),
    replyRules: $("fishReplyRules"),
    audience: $("fishAudiencePrompt"),
    trigger: $("fishTriggerPrompt"),
    dailyLimit: $("fishDailyLimit"),
    minInterval: $("fishMinInterval"),
    activeHours: $("fishActiveHours"),
    perVideoLimit: $("fishPerVideoLimit"),
    maxChars: $("fishMaxChars"),
    maxCandidates: $("fishMaxCandidates"),
    commentsPerVideo: $("fishCommentsPerVideo"),
    minLikes: $("fishMinLikes"),
    minViews: $("fishMinViews"),
    minAgeDays: $("fishMinAgeDays"),
    blockedWords: $("fishBlockedWords"),
    allowTopLevel: $("fishAllowTopLevel"),
    notifyOwner: $("fishNotifyOwner"),
    ownerName: $("fishOwnerName"),
    ownerMid: $("fishOwnerMid"),
    notifyDailyLimit: $("fishNotifyDailyLimit"),
    notifyChannel: $("fishNotifyChannel"),
    saveConfig: $("fishSaveConfigBtn"),
    preview: $("fishPreviewBtn"),
    run: $("fishRunBtn"),
    runResult: $("fishRunResult"),
    ledger: $("fishLedger"),
    report: $("fishReport"),
  };

  if (!el.enabled || !el.saveConfig) {
    // Panel not present (e.g. an older cached index.html) — nothing to do.
    return;
  }

  let loaded = false;
  let busy = false;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function api(path, options) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      ...options,
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const message =
        (payload && (payload.message || payload.detail)) ||
        `${response.status} ${response.statusText}`;
      throw new Error(String(message));
    }
    return payload || {};
  }

  function numberValue(input, fallback) {
    const parsed = Number(String(input?.value || "").trim());
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function collectConfig() {
    return {
      enabled: Boolean(el.enabled.checked),
      dry_run: Boolean(el.dryRun.checked),
      persona_name: String(el.personaName.value || "").trim(),
      persona_prompt: String(el.personaPrompt.value || ""),
      reply_rules: String(el.replyRules.value || ""),
      audience_prompt: String(el.audience?.value || ""),
      trigger_prompt: String(el.trigger?.value || ""),
      daily_limit: numberValue(el.dailyLimit, 3),
      min_interval_minutes: numberValue(el.minInterval, 30),
      active_hours: String(el.activeHours?.value || "").trim(),
      per_video_limit: numberValue(el.perVideoLimit, 1),
      max_chars: numberValue(el.maxChars, 80),
      max_candidates: numberValue(el.maxCandidates, 5),
      comments_per_video: numberValue(el.commentsPerVideo, 10),
      min_comment_likes: numberValue(el.minLikes, 0),
      min_view_count: numberValue(el.minViews, 0),
      min_publish_age_days: numberValue(el.minAgeDays, 0),
      blocked_words: String(el.blockedWords.value || "")
        .split(/[,，\s]+/)
        .map((word) => word.trim())
        .filter(Boolean),
      allow_top_level: Boolean(el.allowTopLevel.checked),
      notify_owner: Boolean(el.notifyOwner?.checked),
      owner_name: String(el.ownerName?.value || "").trim(),
      owner_mid: numberValue(el.ownerMid, 0),
      notify_daily_limit: numberValue(el.notifyDailyLimit, 1),
      notify_channel: String(el.notifyChannel?.value || "comment"),
    };
  }

  function fillConfig(config) {
    if (!config) {
      return;
    }
    el.enabled.checked = Boolean(config.enabled);
    el.dryRun.checked = Boolean(config.dry_run);
    el.personaName.value = config.persona_name || "";
    el.personaPrompt.value = config.persona_prompt || "";
    el.replyRules.value = config.reply_rules || "";
    if (el.audience) {
      el.audience.value = config.audience_prompt || "";
    }
    if (el.trigger) {
      el.trigger.value = config.trigger_prompt || "";
    }
    el.dailyLimit.value = String(config.daily_limit ?? 3);
    el.minInterval.value = String(config.min_interval_minutes ?? 30);
    if (el.activeHours) {
      el.activeHours.value = config.active_hours || "";
    }
    el.perVideoLimit.value = String(config.per_video_limit ?? 1);
    el.maxChars.value = String(config.max_chars ?? 80);
    el.maxCandidates.value = String(config.max_candidates ?? 5);
    el.commentsPerVideo.value = String(config.comments_per_video ?? 10);
    el.minLikes.value = String(config.min_comment_likes ?? 0);
    if (el.minViews) {
      el.minViews.value = String(config.min_view_count ?? 0);
    }
    if (el.minAgeDays) {
      el.minAgeDays.value = String(config.min_publish_age_days ?? 0);
    }
    el.blockedWords.value = (config.blocked_words || []).join(", ");
    el.allowTopLevel.checked = Boolean(config.allow_top_level);
    if (el.notifyOwner) {
      el.notifyOwner.checked = Boolean(config.notify_owner);
    }
    if (el.ownerName) {
      el.ownerName.value = config.owner_name || "";
    }
    if (el.ownerMid) {
      el.ownerMid.value = String(config.owner_mid ?? 0);
    }
    if (el.notifyDailyLimit) {
      el.notifyDailyLimit.value = String(config.notify_daily_limit ?? 1);
    }
    if (el.notifyChannel) {
      el.notifyChannel.value = String(config.notify_channel || "comment");
    }
  }

  function renderStatus(payload) {
    const account = payload?.account || {};
    const state = payload?.state || {};
    const config = payload?.config || {};
    const parts = [];
    if (!config.enabled) {
      parts.push("实验室未启用");
    } else if (account.authenticated && account.isolated) {
      parts.push(`已登录：${account.uname || "未命名"}（mid ${account.mid || "?"}）`);
    } else {
      parts.push(account.message || "大肥鱼还没有登录");
    }
    parts.push(`等级 ${state.level || 1}`);
    parts.push(`今日 ${state.today_published || 0}/${config.daily_limit ?? 0} 条`);
    if (state.today_notified) {
      parts.push(`已通知 ${state.today_notified} 次`);
    }
    if (config.dry_run) {
      parts.push("预览模式");
    }
    if (el.statusLine) {
      el.statusLine.textContent = parts.join(" · ");
    }
    if (el.accountStatus) {
      el.accountStatus.textContent = account.message || "";
    }
    renderLedger(payload?.recent || []);
    if (el.report) {
      el.report.textContent = payload?.today_report || "今天还没有记录。";
    }
  }

  function renderLedger(rows) {
    if (!el.ledger) {
      return;
    }
    if (!rows.length) {
      el.ledger.innerHTML = '<li style="opacity:.7">还没有记录。</li>';
      return;
    }
    el.ledger.innerHTML = rows
      .map((row) => {
        const status = String(row.status || "");
        const when = String(row.ts || "").replace("T", " ").slice(0, 16);
        const title = escapeHtml(row.video_title || row.bvid || "");
        const reply = escapeHtml(row.reply || "");
        const error = escapeHtml(row.error || "");
        const link = row.bvid
          ? ` <a href="https://www.bilibili.com/video/${encodeURIComponent(row.bvid)}" target="_blank" rel="noopener">${escapeHtml(row.bvid)}</a>`
          : "";
        return `<li style="padding:8px 0;border-bottom:1px solid rgba(0,0,0,.06)">
          <div><b>${escapeHtml(status)}</b> · ${escapeHtml(when)} · ${title}${link}</div>
          ${reply ? `<div style="margin-top:4px">${reply}</div>` : ""}
          ${error ? `<div style="margin-top:4px;color:#b45309">${error}</div>` : ""}
        </li>`;
      })
      .join("");
  }

  async function loadAll() {
    try {
      const [configPayload, statusPayload] = await Promise.all([
        api("/api/fish/config"),
        api("/api/fish/status"),
      ]);
      fillConfig(configPayload.config);
      renderStatus(statusPayload);
      loaded = true;
      if (el.runResult) {
        el.runResult.textContent = "";
      }
    } catch (error) {
      if (el.statusLine) {
        el.statusLine.textContent = `读取失败：${error.message}`;
      }
    }
  }

  async function withBusy(button, label, task) {
    if (busy) {
      return;
    }
    busy = true;
    const previous = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = label;
    }
    try {
      await task();
    } finally {
      busy = false;
      if (button) {
        button.disabled = false;
        button.textContent = previous;
      }
    }
  }

  el.saveConfig.addEventListener("click", () => {
    void withBusy(el.saveConfig, "保存中…", async () => {
      try {
        const payload = await api("/api/fish/config", {
          method: "PUT",
          body: JSON.stringify(collectConfig()),
        });
        fillConfig(payload.config);
        if (el.runResult) {
          el.runResult.textContent = "设置已保存。";
        }
        await loadAll();
      } catch (error) {
        if (el.runResult) {
          el.runResult.textContent = `保存失败：${error.message}`;
        }
      }
    });
  });

  el.saveAccount?.addEventListener("click", () => {
    void withBusy(el.saveAccount, "校验中…", async () => {
      try {
        const payload = await api("/api/fish/account", {
          method: "POST",
          body: JSON.stringify({ cookie: String(el.cookie.value || "").trim() }),
        });
        el.cookie.value = "";
        if (el.accountStatus) {
          el.accountStatus.textContent = payload.message || "已保存。";
        }
        await loadAll();
      } catch (error) {
        if (el.accountStatus) {
          el.accountStatus.textContent = `保存失败：${error.message}`;
        }
      }
    });
  });

  async function pollQrLogin(qrcodeKey) {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      let payload = null;
      try {
        payload = await api(
          `/api/fish/account/qrcode/poll?qrcode_key=${encodeURIComponent(qrcodeKey)}`,
          { method: "GET" },
        );
      } catch (error) {
        el.qrStatus.textContent = `查询失败：${error.message}`;
        return;
      }
      const status = String(payload.status || "");
      if (status === "scanned") {
        el.qrStatus.textContent = "已扫码，请在手机上确认登录。";
        continue;
      }
      if (status === "logged_in") {
        el.qrStatus.textContent = payload.message || "登录成功。";
        el.qrBox.hidden = true;
        await loadAll();
        return;
      }
      if (status === "expired" || status === "rejected" || payload.ok === false) {
        el.qrStatus.textContent = payload.message || "二维码已失效，再点一次「扫码登录」。";
        el.qrBox.hidden = true;
        return;
      }
    }
    el.qrStatus.textContent = "二维码超时了，再点一次「扫码登录」。";
    el.qrBox.hidden = true;
  }

  el.qrLogin?.addEventListener("click", () => {
    if (!el.qrStatus || !el.qrImage || !el.qrBox) {
      // Older cached HTML without the QR block: the button would be a no-op.
      return;
    }
    void withBusy(el.qrLogin, "生成中…", async () => {
      el.qrStatus.textContent = "正在生成二维码…";
      try {
        const payload = await api("/api/fish/account/qrcode", {
          method: "POST",
          body: "{}",
        });
        el.qrImage.src = payload.qrcode_image;
        el.qrBox.hidden = false;
        el.qrStatus.textContent = "用手机 B站 扫这张码，确认时选「记住我 / 长期」。";
        await pollQrLogin(payload.qrcode_key);
      } catch (error) {
        el.qrStatus.textContent = `二维码生成失败：${error.message}`;
      }
    });
  });

  el.clearAccount?.addEventListener("click", () => {
    void withBusy(el.clearAccount, "清除中…", async () => {
      try {
        await api("/api/fish/account", { method: "DELETE" });
        if (el.accountStatus) {
          el.accountStatus.textContent = "已清除大肥鱼的登录。";
        }
        await loadAll();
      } catch (error) {
        if (el.accountStatus) {
          el.accountStatus.textContent = `清除失败：${error.message}`;
        }
      }
    });
  });

  function bindRun(button, dryRun, label) {
    button?.addEventListener("click", () => {
      void withBusy(button, "运行中…", async () => {
        if (el.runResult) {
          el.runResult.textContent = "正在跑一轮…";
        }
        try {
          const payload = await api("/api/fish/run", {
            method: "POST",
            body: JSON.stringify({ dry_run: dryRun, trigger: "manual" }),
          });
          if (el.runResult) {
            el.runResult.textContent = payload.message || String(payload.status || label);
          }
          await loadAll();
        } catch (error) {
          if (el.runResult) {
            el.runResult.textContent = `运行失败：${error.message}`;
          }
        }
      });
    });
  }

  bindRun(el.preview, true, "预览完成");
  bindRun(el.run, false, "已发送");

  // Lazy: the settings page ships with every desktop page load, so do not
  // spend two requests until the user actually opens this tab.
  document.getElementById("settingsTabFish")?.addEventListener("click", () => {
    if (!loaded) {
      void loadAll();
    }
  });

  // Expose a tiny hook for manual testing from the console.
  window.__fishLab = { reload: loadAll, loaded: () => loaded };
})();
